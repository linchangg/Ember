#!/usr/bin/env python3
"""Small, slow specification smoke test; NOT the Rust production engine.
Checks the declared LF/Euler update against known circle/ellipse geometry.
Default --quick uses coarse grids. --full adds 5 m circle and 10 m ellipse.
"""
import argparse
import json
import math
from pathlib import Path


def evolve(phi,n,dx,steps,coeff,max_step=30):
    d0,d1,a0,a1,b0,b1=coeff
    ax=abs(d0)+math.hypot(a0,b0)
    ay=abs(d1)+math.hypot(a1,b1)
    cfl=.4/((ax+ay)/dx) if ax+ay else math.inf
    t=0;count=0
    for end,coeff in steps:
        d0,d1,a0,a1,b0,b1=coeff
        ax=abs(d0)+math.hypot(a0,b0)
        ay=abs(d1)+math.hypot(a1,b1)
        cfl=.4/((ax+ay)/dx) if ax+ay else math.inf
        while t<end-1e-10:
            dt=min(cfl,max_step,end-t)
            new=phi.copy()
            for r in range(n):
                base=r*n
                for c in range(n):
                    i=base+c;v=phi[i]
                    west=phi[i-1] if c else v
                    east=phi[i+1] if c+1<n else v
                    south=phi[i-n] if r else v
                    north=phi[i+n] if r+1<n else v
                    dm=(v-west)/dx;dp=(east-v)/dx
                    em=(v-south)/dx;ep=(north-v)/dx
                    px=(dm+dp)*.5;py=(em+ep)*.5
                    H=d0*px+d1*py+math.hypot(a0*px+a1*py,b0*px+b1*py)
                    numerical=H-.5*ax*(dp-dm)-.5*ay*(ep-em)
                    new[i]=min(v,v-dt*numerical)
            phi=new;t+=dt;count+=1
    return phi,count


def points(phi,n,dx):
    out=[]
    for r in range(n):
        for c in range(n):
            i=r*n+c;v=phi[i]
            for j,xx,yy in ([(i+1,dx,0)] if c+1<n else [])+([(i+n,0,dx)] if r+1<n else []):
                w=phi[j]
                if (v<=0<w) or (w<=0<v):
                    q=v/(v-w)
                    out.append(((c+.5)*dx+q*xx,(r+.5)*dx+q*yy))
    return out


def circle(dx):
    n=round(2000/dx);center=1000
    phi=[math.hypot((c+.5)*dx-center,(r+.5)*dx-center)-150 for r in range(n) for c in range(n)]
    coeff=(0,0,.1,0,0,.1)
    result,count=evolve(phi,n,dx,[(1200,coeff)],coeff)
    boundary=points(result,n,dx)
    errors=[abs(math.hypot(x-center,y-center)-270) for x,y in boundary]
    predicted=sum(v<=0 for v in result)*dx*dx
    relative=abs(predicted-math.pi*270**2)/(math.pi*270**2)
    assert max(errors)<=2*dx,(dx,max(errors))
    if dx==10: assert relative<=.08,relative
    return dict(case='circle',dx_m=dx,steps=count,max_error_m=max(errors),mean_error_m=sum(errors)/len(errors),relative_area_error=relative)


def ellipse(dx,change=False):
    n=round(4000/dx);center=2000
    a,b=.2,.1;c=math.sqrt(a*a-b*b)
    east=(c,0,a,0,0,b);north=(0,c,0,a,-b,0)
    if change:
        phi=[math.hypot((j+.5)*dx-center,(r+.5)*dx-center)-150 for r in range(n) for j in range(n)]
        schedule=[(300,east),(600,north)]
    else:
        t0=1500
        phi=[b*(math.hypot(((j+.5)*dx-center-c*t0)/a,((r+.5)*dx-center)/b)-t0) for r in range(n) for j in range(n)]
        schedule=[(600,east)]
    result,count=evolve(phi,n,dx,schedule,east)
    boundary=points(result,n,dx)
    errors=[]
    for k in range(72):
        angle=2*math.pi*k/72;nx,ny=math.cos(angle),math.sin(angle)
        pred=max((x-center)*nx+(y-center)*ny for x,y in boundary)
        he=c*nx+math.hypot(a*nx,b*ny)
        hn=c*ny+math.hypot(a*ny,b*nx)
        truth=150+300*(he+hn) if change else (1500+600)*he
        errors.append(abs(pred-truth))
    assert max(errors)<=(3 if change else 2)*dx,(change,dx,max(errors))
    return dict(case='weather_switch' if change else 'ellipse',dx_m=dx,steps=count,max_support_error_m=max(errors),mean_support_error_m=sum(errors)/len(errors))


def main():
    p=argparse.ArgumentParser();p.add_argument('--full',action='store_true');p.add_argument('--quick',action='store_true');args=p.parse_args()
    rows=[]
    for dx in ([20,10,5] if args.full else [20,10]):
        row=circle(dx);rows.append(row);print(row,flush=True)
    assert rows[1]['mean_error_m']<rows[0]['mean_error_m']
    if args.full: assert rows[2]['mean_error_m']<rows[1]['mean_error_m']
    for changed in [False,True]:
        row=ellipse(10 if args.full else 20,changed);rows.append(row);print(row,flush=True)
    target=Path(__file__).resolve().parents[2]/'fixtures/spec/numerical_smoke_results.json'
    target.write_text(json.dumps({'notice':'Python specification smoke tests, not validation of an implemented Rust engine.', 'mode':'full' if args.full else 'quick','results':rows},indent=2)+'\n')
    print('PASS: specification numerical smoke tests',flush=True)


if __name__=='__main__':main()
