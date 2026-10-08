"""Measure only the six acquired M08A one-page Future Corp vector PDFs.

Use the existing bundled Python with pypdf/pdfplumber. --check verifies the saved
measurement record without changing it. This does not decode LDS cut geometry.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
from pypdf.generic import ContentStream

ROOT = Path(__file__).resolve().parent
TOLERANCE_MM = 0.001
PT_TO_MM = 25.4 / 72


def close(actual, expected):
    assert len(actual) == len(expected)
    assert all(abs(a-b) <= TOLERANCE_MM for a, b in zip(actual, expected)), (actual, expected)


def bounds(points):
    xs, ys = zip(*points)
    return [min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)]


def expected_path(shape):
    if shape['kind'] == 'rect':
        x, y, w, h = shape['xywh_mm']
        return [[x, y], [x+w, y], [x+w, y+h], [x, y+h]]
    return shape['vertices_mm']


def measure_run(run, expected):
    artifact = run['print_output']
    path = ROOT / artifact['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    page = reader.pages[0]
    explicit_page_boxes = [key for key in ('/MediaBox', '/CropBox', '/BleedBox', '/TrimBox', '/ArtBox') if key in page]
    assert page.get('/Rotate', 0) == 0 and page.get('/UserUnit', 1) == 1
    assert list(page.mediabox) == list(page.cropbox)
    ops = ContentStream(page.get_contents(), reader).operations
    assert ops[0][1] == b'q' and ops[-1][1] == b'Q'
    assert sum(op == b'q' for _, op in ops) == sum(op == b'Q' for _, op in ops) == 1
    transforms = [[float(v) for v in args] for args, op in ops if op == b'cm']
    assert len(transforms) == 1
    a, b, c, d, e, f = transforms[0]
    height = float(page.mediabox.height)
    assert a > 0 and d < 0 and b == c == e == 0 and f == height
    assert abs(a+d) < 1e-12

    def resolve(values):
        return [[(a*x+c*y+e)*PT_TO_MM, (height-b*x-d*y-f)*PT_TO_MM]
                for x, y in zip(values[::2], values[1::2])]

    paths, current = [], []
    for args, op in ops:
        assert op in {b'q', b'Q', b'cm', b'CS', b'rg', b'm', b'l', b'c', b'h', b'f*'}, op
        if op == b'rg':
            assert list(args) == [0, 0, 0]
        elif op in {b'm', b'l', b'c'}:
            if op == b'm':
                assert not current
            current.append({'operator': op.decode(), 'points': resolve(list(map(float, args)))})
        elif op == b'h':
            current.append({'operator': 'h', 'points': []})
        elif op == b'f*':
            assert current[0]['operator'] == 'm' and current[-1]['operator'] == 'h'
            points = [point for command in current for point in command['points']]
            paths.append({'bounds_xywh_mm': bounds(points), 'resolved_path_mm': current})
            current = []
    assert not current and len(paths) == 12  # Four artwork objects, then eight bars; no image/form operators.
    for name, observed in zip(['rectangle', 'circle', 'triangle', 'ell'], paths[:4]):
        observed['shape_id'] = name
        source = expected['shapes'][name]
        commands = observed['resolved_path_mm']
        if name == 'circle':
            cx, cy = source['center_mm']
            r = source['radius_mm']
            k = 4*(math.sqrt(2)-1)/3*r
            close(observed['bounds_xywh_mm'], [cx-r, cy-r, 2*r, 2*r])
            assert [command['operator'] for command in commands] == ['m', 'c', 'c', 'c', 'c', 'h']
            wanted = [[cx-r, cy], [cx-r, cy-k], [cx-k, cy-r], [cx, cy-r],
                      [cx+k, cy-r], [cx+r, cy-k], [cx+r, cy], [cx+r, cy+k],
                      [cx+k, cy+r], [cx, cy+r], [cx-k, cy+r], [cx-r, cy+k], [cx-r, cy]]
            for actual, target in zip([p for command in commands for p in command['points']], wanted):
                close(actual, target)
        else:
            assert all(command['operator'] in {'m', 'l', 'h'} for command in commands)
            vertices = [p for command in commands for p in command['points']]
            if vertices[-1] == vertices[0]:
                vertices.pop()
            wanted = expected_path(source)
            assert len(vertices) == len(wanted)
            for actual, target in zip(vertices, wanted):
                close(actual, target)
        observed['rectangle_relative_displacement_mm'] = [
            observed['bounds_xywh_mm'][i]-paths[0]['bounds_xywh_mm'][i] for i in (0, 1)]
    for bar in paths[4:]:
        assert len(bar['resolved_path_mm']) == 5
        width, height_mm = bar['bounds_xywh_mm'][2:]
        close(sorted([width, height_mm]), [1, 13])

    # Independently resolve the same objects with pdfminer/pdfplumber, matching by bounds.
    with pdfplumber.open(path) as pdf:
        parsed = pdf.pages[0]
        assert not parsed.images and not parsed.lines
        independent = parsed.rects + parsed.curves
        assert len(independent) == 12
        independent_bounds = []
        for obj in independent:
            assert obj['fill'] and not obj['stroke']
            independent_bounds.append([obj[key]*PT_TO_MM for key in ('x0', 'top', 'width', 'height')])
        for observed in paths:
            candidate = min(independent_bounds,
                            key=lambda values: max(abs(a-b) for a, b in zip(values, observed['bounds_xywh_mm'])))
            close(candidate, observed['bounds_xywh_mm'])
            independent_bounds.remove(candidate)
    group_points = []
    for obj in paths[:4]:
        x, y, w, h = obj['bounds_xywh_mm']
        group_points.extend([[x, y], [x+w, y+h]])
    group = bounds(group_points)
    x0, y0, x1, y1 = expected['occupied_bounds_mm']
    close(group, [x0, y0, x1-x0, y1-y0])
    return {
        'id': run['id'], 'source_pdf': artifact, 'source_probe_id': run['source_probe_id'],
        'requested_page_mm': expected['page_mm'], 'accepted_native_page_mm': None,
        'page_boxes_pt': {key: list(map(float, getattr(page, key)))
                          for key in ('mediabox', 'cropbox', 'bleedbox', 'trimbox', 'artbox')},
        'explicit_page_boxes': explicit_page_boxes,
        'emitted_page_mm': [float(page.mediabox.width)*PT_TO_MM, float(page.mediabox.height)*PT_TO_MM],
        'rotation_degrees': int(page.get('/Rotate', 0)), 'rotation_explicit': '/Rotate' in page,
        'user_unit': float(page.get('/UserUnit', 1)), 'user_unit_explicit': '/UserUnit' in page,
        'producer': reader.metadata.get('/Producer'), 'creator': reader.metadata.get('/Creator'),
        'content_cm_operations': transforms, 'artwork': paths[:4], 'group_bounds_xywh_mm': group,
        'registration_bars': paths[4:], 'independent_pdfplumber_bounds_match': True,
    }


def measure():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    cases = {case['id']: case['expected'] for case in manifest['cases']}
    runs = manifest['numeric_placement_evidence']['acquisitions']
    assert len(runs) == 6
    outputs = [measure_run(run, cases[run['source_probe_id']]) for run in runs]
    by_id = {output['id']: output for output in outputs}
    comparisons = []
    for left, right in [('letter-base-initial', 'letter-base-repeat'),
                        ('letter-shifted-initial', 'letter-shifted-repeat'),
                        ('a4-page-before-import', 'a4-page-after-import')]:
        x, y = by_id[left], by_id[right]
        assert (ROOT/x['source_pdf']['path']).read_bytes() == (ROOT/y['source_pdf']['path']).read_bytes()
        assert x['artwork'] == y['artwork'] and x['registration_bars'] == y['registration_bars']
        comparisons.append({'left': left, 'right': right, 'pdf_byte_identical': True,
                            'artwork_and_bars_identical': True})
    for suffix in ('initial', 'repeat'):
        base, shifted = by_id['letter-base-'+suffix], by_id['letter-shifted-'+suffix]
        assert base['source_pdf']['sha256'] != shifted['source_pdf']['sha256']
        assert base['registration_bars'] == shifted['registration_bars']
        deltas = []
        for source, target in zip(base['artwork'], shifted['artwork']):
            source_points = [p for command in source['resolved_path_mm'] for p in command['points']]
            target_points = [p for command in target['resolved_path_mm'] for p in command['points']]
            assert len(source_points) == len(target_points)
            for x, y in zip(source_points, target_points):
                close([y[0]-x[0], y[1]-x[1]], [12, 9])
            deltas.append([target['bounds_xywh_mm'][i]-source['bounds_xywh_mm'][i] for i in (0, 1)])
        comparisons.append({'letter_pair': suffix, 'shape_left_top_deltas_mm': deltas,
                            'every_path_point_matches_12_9_mm': True, 'registration_bars_identical': True})
    return {'tools': {name: importlib.metadata.version(name) for name in ('pypdf', 'pdfplumber')},
            'tolerance_mm': TOLERANCE_MM, 'classification': 'locally reproduced printed vector geometry; no LDS cut decoding',
            'outputs': outputs, 'identity_comparisons': comparisons}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    result = measure()
    output = ROOT/'numeric-placement-measurements.json'
    if options.check:
        assert json.loads(output.read_text()) == result, 'Saved measurements differ from local reproduction'
    else:
        output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    for run in result['outputs']:
        print(f"PASS {run['id']}: rectangle {run['artwork'][0]['bounds_xywh_mm']}; four vectors including L; eight bars")
    print('PASS three byte-identity comparisons; both Letter pairs preserve (+12,+9) mm with fixed bars; independent pdfplumber bounds')
