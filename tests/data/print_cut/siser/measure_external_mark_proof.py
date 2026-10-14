"""Bounded straight-vector checker for the M08B Letter fixture; no fitted alignment."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ContentStream

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / 'references/external-marks/nine-card-baseline'
RECORD = REFERENCE / 'reference.json'
SOURCE_HASHES = {
    'Nine Card Baseline Cut.pdf': '7023f253a4e29caad51e25c8a7b0176c7be179b062cbb77cb49a9789d1b9bc68',
    'Nine Card Baseline Cut.lds': '48abbd086c354eff398b2d54d864e4f5174a0220defd634f82318219a6e63eca',
}
TOLERANCE_MM = .01
STROKE_TOLERANCE_MM = .00001
PT_TO_MM = 25.4 / 72


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bounds(points):
    xs, ys = zip(*points)
    return [min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)]


def compose(old, new):
    a,b,c,d,e,f = old
    g,h,i,j,k,l = new
    return [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f]


def rectangle(points):
    """Normalize cyclic start and direction, retaining actual corner coordinates."""
    if len(points) > 1 and points[-1] == points[0]:
        points = points[:-1]
    require(len(points) == 4, 'Expected four straight rectangle corners')
    x,y,w,h = bounds(points)
    require(w > 0 and h > 0, 'Degenerate rectangle')
    expected = [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]
    require(all(any(math.dist(p,q) < 1e-7 for q in expected) for p in points), 'Non-axis-aligned rectangle')
    require(len(set(map(tuple, points))) == 4, 'Repeated rectangle vertex')
    for p,q in zip(points, points[1:]+points[:1]):
        require(abs(p[0]-q[0]) < 1e-7 or abs(p[1]-q[1]) < 1e-7, 'Diagonal rectangle edge')
    return [list(p) for p in expected]


def measure(path):
    reader = PdfReader(path)
    require(len(reader.pages) == 1, f'{path}: expected one page')
    page = reader.pages[0]
    keys = ('/MediaBox','/CropBox','/BleedBox','/TrimBox','/ArtBox')
    explicit = {k: list(map(float,page[k])) for k in keys if k in page}
    rotation = page.get('/Rotate')
    user_unit = page.get('/UserUnit')
    effective_rotation = 0 if rotation is None else float(rotation)
    effective_user_unit = 1 if user_unit is None else float(user_unit)
    require(effective_rotation == 0 and effective_user_unit == 1, 'Unexpected Rotate/UserUnit')
    effective = {k:list(map(float,getattr(page,k[1:].lower()))) for k in keys}
    require(all(v == [0,0,612,792] for v in effective.values()), 'Unexpected page boundary or cropping')
    require('/Annots' not in page or not page['/Annots'], 'Unexpected annotations')
    resources = page.get('/Resources', {})
    require(not resources.get('/XObject'), 'Unexpected image or form artwork')
    require(not resources.get('/Shading') and not resources.get('/Pattern'), 'Unexpected shading/pattern')
    state = dict(ctm=[1,0,0,1,0,0], width=1., cap=0, join=0, miter=10.,
                 stroke=[0.], fill=[0.], dash=[], clips=[])
    stack, points, closed, clip_pending = [], [], False, False
    contours, bars = [], []
    def point(x,y):
        a,b,c,d,e,f = state['ctm']
        return [(a*x+c*y+e)*PT_TO_MM, (792-b*x-d*y-f)*PT_TO_MM]
    for args, op in ContentStream(page.get_contents(), reader).operations:
        nums = lambda: list(map(float,args))
        if op == b'q':
            stack.append(copy.deepcopy(state))
        elif op == b'Q':
            require(bool(stack), 'Unbalanced Q'); state = stack.pop()
        elif op == b'cm':
            state['ctm'] = compose(state['ctm'],nums())
        elif op in (b'CS',b'cs'):
            space = args[0]
            if str(space) not in ('/DeviceRGB','/DeviceGray'):
                space = resources['/ColorSpace'][space]
            require(str(space) in ('/DeviceRGB','/DeviceGray'), 'Unexpected color space')
        elif op in (b'RG',b'G',b'rg',b'g',b'SCN',b'scn'):
            color = nums(); require(all(v == 0 for v in color), 'Nonblack paint')
            state['stroke' if op in (b'RG',b'G',b'SCN') else 'fill'] = color
        elif op in (b'w',b'J',b'j',b'M'):
            state[{b'w':'width',b'J':'cap',b'j':'join',b'M':'miter'}[op]] = float(args[0])
        elif op == b'd':
            require(not args[0] and float(args[1]) == 0, 'Dashed paint'); state['dash'] = []
        elif op == b'gs':
            gs = resources['/ExtGState'][args[0]]
            require(set(gs).issubset({'/Type','/CA','/ca','/SMask','/BM','/AIS','/SA','/SM'}), 'Unsupported graphics state')
            require(float(gs.get('/CA',1)) == float(gs.get('/ca',1)) == 1, 'Transparency')
            require(gs.get('/SMask','/None') == '/None' and gs.get('/BM','/Normal') == '/Normal', 'Mask/blend')
            require('/AIS' not in gs or gs['/AIS'].value is False, 'Alpha source')
        elif op == b'm':
            require(not points, 'Multiple subpaths unsupported'); points = [point(*nums())]
        elif op == b'l':
            require(bool(points), 'Line without move'); points.append(point(*nums()))
        elif op == b're':
            require(not points, 'Multiple rectangles unsupported')
            x,y,w,h = nums(); points = [point(x,y),point(x+w,y),point(x+w,y+h),point(x,y+h)]; closed = True
        elif op == b'h':
            closed = True
        elif op in (b'W',b'W*'):
            require(closed, 'Open clip'); clip_pending = True
        elif op in (b'S',b's',b'f',b'f*',b'n'):
            if clip_pending:
                require(op == b'n', 'Painted clip unsupported')
                clip = bounds(rectangle(points))
                require(clip[0] <= 1e-5 and clip[1] <= 1e-5 and
                        clip[0]+clip[2] >= 215.9-1e-5 and clip[1]+clip[3] >= 279.4-1e-5,
                        'Clip smaller than nominal page')
                state['clips'].append(clip)
            elif op != b'n':
                require(closed or op == b's', 'Open painted path')
                corners = rectangle(points)
                item = dict(bounds_xywh_mm=bounds(corners), corners_mm=corners)
                if op in (b'S',b's'):
                    a,b,c,d,_,_ = state['ctm']; scale = math.hypot(a,b)
                    require(abs(scale-math.hypot(c,d)) < 1e-7 and abs(a*c+b*d) < 1e-7, 'Nonuniform stroke transform')
                    require(state['cap'] == state['join'] == 0, 'Expected flat caps/miter joins')
                    item.update(stroke_width_mm=state['width']*scale*PT_TO_MM,
                                cap=state['cap'], join=state['join'], miter_limit=state['miter'],
                                color='black', fill='none', dash=[])
                    contours.append(item)
                else:
                    item.update(color='black', stroke='none'); bars.append(item)
            points, closed, clip_pending = [], False, False
        else:
            raise ValueError(f'Unsupported operator {op!r}; checker only accepts these fixture vector paths')
    require(not stack and not points and not clip_pending, 'Unfinished graphics state/path')
    require(len(contours) == 9 and len(bars) == 8, f'Expected 9 contours/8 bars, got {len(contours)}/{len(bars)}')
    contours.sort(key=lambda x:(round(x['bounds_xywh_mm'][1],5), x['bounds_xywh_mm'][0]))
    bars.sort(key=lambda x:tuple(round(v,5) for v in x['bounds_xywh_mm']))
    return dict(explicit_page_boxes_pt=explicit, effective_page_boxes_pt=effective,
                rotation_explicit=rotation, rotation_effective=effective_rotation,
                user_unit_explicit=user_unit, user_unit_effective=effective_user_unit,
                measured_paper_mm=[215.9,279.4], contours=contours, bars=bars)


def compare(reference, proof):
    errors, coordinate_max, stroke_max = [], 0., 0.
    individual = []
    for group in ('contours','bars'):
        if len(reference[group]) != len(proof[group]):
            errors.append(f'{group}: count differs'); continue
        for index,(a,b) in enumerate(zip(reference[group],proof[group]),1):
            difference = max(abs(x-y) for p,q in zip(a['corners_mm'],b['corners_mm']) for x,y in zip(p,q))
            coordinate_max = max(coordinate_max,difference)
            individual.append(dict(kind=group,index=index,maximum_coordinate_difference_mm=difference))
            if difference > TOLERANCE_MM:
                errors.append(f'{group} {index}: coordinate difference {difference} mm')
            if group == 'contours':
                difference = abs(a['stroke_width_mm']-b['stroke_width_mm']); stroke_max = max(stroke_max,difference)
                individual[-1]['stroke_width_difference_mm'] = difference
                if difference > STROKE_TOLERANCE_MM:
                    errors.append(f'contour {index}: stroke difference {difference} mm')
    return dict(passed=not errors, failures=errors, maximum_coordinate_difference_mm=coordinate_max,
                maximum_stroke_width_difference_mm=stroke_max,
                coordinate_tolerance_mm=TOLERANCE_MM, stroke_tolerance_mm=STROKE_TOLERANCE_MM,
                individual_comparisons=individual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-reference', action='store_true')
    parser.add_argument('--proof', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    for name,wanted in SOURCE_HASHES.items():
        require(digest(REFERENCE/name) == wanted, f'Baseline hash mismatch: {name}')
    reference = measure(REFERENCE/'Nine Card Baseline Cut.pdf')
    if args.record_reference:
        require(not RECORD.exists(), 'Refusing to overwrite reference record')
        data = dict(fixture_id='m08b-letter-nine-card-baseline', requested_paper_mm=[215.9,279.4],
                    sources={name:dict(path=str((REFERENCE/name).relative_to(ROOT)).replace('\\','/'),sha256=h)
                             for name,h in SOURCE_HASHES.items()}, measurement=reference,
                    classification='fixture-specific vector measurements; operator-reported physical observations',
                    physical_observations=[
                        dict(stage='pre-recalibration first cut', observation='Approximately 0.33 mm left; bottom edges approximately 0.9 mm high across rows; nine pieces approximately 63 x 88 mm'),
                        dict(stage='pre-recalibration second cut', observation='Top row approximately 0.4 mm left; bottom row visually aligned; approximately 0.5 mm high, middle row approximately 0.7 mm high'),
                        dict(stage='post-Siser-recalibration', observation='Unchanged baseline printed/cut again; cuts visually coincided with printed lines; small deviations not reliably quantified')],
                    qualification='Caliper alignment was visual; measurement repeatability not established. Calibration contributed materially; no universal accuracy specification or correction derived.',
                    calibration_date=None, calibration_procedure=None, active_tool_settings=None)
        RECORD.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    else:
        saved = json.loads(RECORD.read_text(encoding='utf-8'))
        require(saved['measurement'] == reference, 'Reference measurement record drift')
        require(args.proof is not None and args.output is not None, '--proof and --output required')
        require(not args.output.exists(), 'Refusing to overwrite comparison evidence')
        proof = measure(args.proof); result = compare(reference,proof)
        negative = copy.deepcopy(proof)
        negative['bars'][0]['corners_mm'][0][0] += .02
        require(not compare(reference,negative)['passed'], 'Perturbation was not detected')
        result.update(reference=reference,proof=proof,negative_coordinate_check='0.02 mm perturbation rejected',
                      output_sha256={args.proof.name:digest(args.proof)},
                      preserved_input_sha256=SOURCE_HASHES, reference_record_sha256=digest(RECORD),
                      pypdf_version=__import__('pypdf').__version__)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('reference','proof','individual_comparisons')},indent=2))
        require(result['passed'], 'Comparison failed: '+ '; '.join(result['failures']))


if __name__ == '__main__':
    main()
