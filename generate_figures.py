#!/usr/bin/env python3
"""Derive every plotted count from the preserved pilot CSV/JSON. Requires Pillow.

Usage: python generate_figures.py [--run results/observed] [--out figures]
The default data/ directory contains byte-for-byte copies of the observed tables.
No experiment is executed and no stochastic resampling is performed.
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
COLORS = {"naive_preflight": "#b45c39", "transactional_gateway": "#236d91"}
LABELS = {"naive_preflight": "Cached preflight", "transactional_gateway": "Transactional gateway"}
SCENARIOS = ["over_budget", "within_budget", "duplicate", "ancestor_revoked"]
NAMES = ["Shared budget", "Within-budget control", "Repeated operation", "Ancestor revocation"]

def font(size: int):
    for path in ["/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)

def truth(value):
    return str(value).lower() == "true"

def centered(draw, x, y, label, size=25, fill="#172634"):
    draw.text((x, y), label, font=font(size), fill=fill, anchor="mt")

def local_plot(summary, out):
    img = Image.new("RGB", (1800, 1120), "white")
    d = ImageDraw.Draw(img)
    d.text((65, 25), "Local concurrency: violations and authorized completion", font=font(42), fill="#172634")
    d.text((65, 82), "20 related authored schedules per scenario and controller; actual committed effects", font=font(27), fill="#52616b")
    for panel, (metric, title) in enumerate([("violations", "Trials with an unauthorized effect (lower is better)"), ("authorized_matches", "Trials matching authorized effect count (higher is better)")]):
        left, right, top, bottom = 120, 1725, 235 + panel*435, 480 + panel*435
        d.text((65, top-80), title, font=font(30), fill="#172634")
        for tick in [0, 5, 10, 15, 20]:
            y = bottom-(bottom-top)*tick/20
            d.line((left, y, right, y), fill="#dce3e8", width=2)
            d.text((left-25, y), str(tick), font=font(24), fill="#52616b", anchor="rm")
        for i, scenario in enumerate(SCENARIOS):
            x=left+(i+.5)*(right-left)/4
            for j, arm in enumerate(COLORS):
                value=summary[scenario][arm][metric]
                bx=x-105+j*108
                by=bottom-(bottom-top)*value/20
                if value: d.rectangle((bx, by, bx+90, bottom), fill=COLORS[arm])
                else: d.line((bx, bottom, bx+90, bottom), fill=COLORS[arm], width=5)
                centered(d,bx+45,by-35,str(value),28)
            centered(d,x,bottom+20,NAMES[i],25)
    for j, arm in enumerate(COLORS):
        x=450+j*560
        d.rectangle((x,1050,x+35,1077),fill=COLORS[arm])
        d.text((x+50,1048),LABELS[arm],font=font(28),fill="#172634")
    img.save(out/"local_results.png", dpi=(300,300))

def boundary_plot(rows, out):
    img=Image.new("RGB",(1800,570),"white");d=ImageDraw.Draw(img)
    d.text((65,25),"External response loss: fewer duplicates, unresolved completion",font=font(40),fill="#172634")
    d.text((65,82),"Two attempts at a separate SQLite endpoint; one authored boundary control per controller",font=font(27),fill="#52616b")
    headings=["Controller","External effects","Unresolved ledger operations","Reserved budget"]
    xs=[65,720,1070,1530]
    for x,h in zip(xs,headings):
        for k,line in enumerate(h.split(" ledger ") if "ledger" in h else [h]):
            d.text((x,165+k*33),line,font=font(27),fill="#52616b")
    for i,r in enumerate(rows):
        y=270+i*115
        d.rectangle((60,y-15,1735,y+70),fill="#f0f5f8")
        d.text((xs[0]+10,y),LABELS[r['arm']],font=font(31),fill=COLORS[r['arm']])
        for x,v in zip(xs[1:],[r['actual_external_effects'],r['unresolved_operations'],r['reserved_amount']]):d.text((x+45,y),str(v),font=font(37),fill="#172634")
    d.text((65,515),"Neither controller confirms task completion. One effect does not establish exactly-once execution.",font=font(26),fill="#52616b")
    img.save(out/"external_boundary.png",dpi=(300,300))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,default=HERE/'data');parser.add_argument('--out',type=Path,default=HERE/'figures');args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    rows=list(csv.DictReader((args.run/'trials.csv').open(newline='')))
    external=json.loads((args.run/'external_boundary.json').read_text())
    expected=json.loads((args.run/'result.json').read_text())['metrics']
    summary={s:{a:{'trials':0,'violations':0,'authorized_matches':0,'effects':0,'effect_amount':0,'ledger_total':0} for a in COLORS} for s in SCENARIOS}
    for r in rows:
        v=summary[r['scenario']][r['arm']];v['trials']+=1;v['violations']+=truth(r['violation']);v['authorized_matches']+=truth(r['authorized_completion_matches']);v['effects']+=int(r['effect_count']);v['effect_amount']+=int(r['effect_amount']);v['ledger_total']+=int(r['ledger_used'])
    totals={a:{k:sum(summary[s][a][k] for s in SCENARIOS) for k in summary[SCENARIOS[0]][a]} for a in COLORS}
    for a in COLORS:
        assert totals[a]['trials']==expected[a]['trials']
        assert totals[a]['violations']==expected[a]['trials_with_violation']
        assert totals[a]['authorized_matches']==expected[a]['authorized_completion_matches']
    out={'source_files':{n:{'sha256':hashlib.sha256((args.run/n).read_bytes()).hexdigest(),'bytes':(args.run/n).stat().st_size} for n in ['trials.csv','external_boundary.json','result.json','worker_traces.json']},'scenario_counts':summary,'totals':totals,'external_boundary':external,'interpretation':'Descriptive counts from related authored schedules; no population confidence intervals or distributed guarantee.'}
    (args.out/'derived_summary.json').write_text(json.dumps(out,indent=2)+'\n')
    local_plot(summary,args.out);boundary_plot(external,args.out)
    print(json.dumps({'totals':totals,'figures':['local_results.png','external_boundary.png'],'summary':'derived_summary.json'},indent=2))

if __name__=='__main__':main()
