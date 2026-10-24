#!/usr/bin/env python3
"""Arithmetic/analytic companion for ENGINE_BUILD_PLAN.md; NOT an independent oracle.

Run from any directory: python3 /absolute/path/docs/spec/reference_math.py
Writes fixtures/spec/reference_values.json; no external packages required.
"""
from pathlib import Path
import csv
import json
import math

ROOT = Path(__file__).resolve().parents[2]


def close(a, b, atol=1e-10, rtol=1e-8):
    assert abs(a-b) <= atol + rtol*max(abs(a), abs(b)), (a,b)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def norm(a):
    return math.sqrt(dot(a,a))


def scale(a, x):
    return tuple(v*x for v in a)


def add(a, b):
    return tuple(x+y for x,y in zip(a,b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(a):
    return scale(a,1/norm(a))


def fuel_catalog():
    with (ROOT/'docs/spec/fuel_models.csv').open() as f:
        return {int(r['id']):r for r in csv.DictReader(f)}


def bin_id(sigma):
    for i, lo in enumerate([1200,192,96,48,16]):
        if sigma >= lo:
            return i
    return None


def scalar(fuel, moisture=(.05,.07,.09,.60,.90), wind_m_s=0, slope_tangent=0):
    """Behavior on aligned wind/slope, customary units internally."""
    m1,m10,m100,mlh,mlw=moisture
    get=lambda k:float(fuel[k])
    load=[get('load_1h_lb_ft2'), get('load_10h_lb_ft2'), get('load_100h_lb_ft2'),
          get('load_live_herb_lb_ft2'), get('load_live_woody_lb_ft2')]
    sig=[get('savr_1h_ft_inv'),109.,30.,get('savr_live_herb_ft_inv'),get('savr_live_woody_ft_inv')]
    ms=[m1,m10,m100,mlh,mlw]
    life=[0,0,0,1,1]
    cured=0.
    original=sum(load)
    if fuel['dynamic']=='true':
        cured=clamp((1.20-mlh)/.90,0,1)
        transferred=load[3]*cured
        load[3]-=transferred
        load.append(transferred); sig.append(sig[3]); ms.append(m1); life.append(0)
    close(sum(load),original,atol=1e-14,rtol=1e-12)
    area=[w*s/32 for w,s in zip(load,sig)]
    group_area=[sum(v for v,l in zip(area,life) if l==g) for g in [0,1]]
    total_area=sum(group_area)
    assert total_area>0
    frac=[ar/group_area[l] if group_area[l]>0 else 0 for ar,l in zip(area,life)]
    F=[ar/total_area for ar in group_area]
    sigma=sum(F[l]*frac[i]*sig[i] for i,l in enumerate(life))
    group_m=[sum(frac[i]*ms[i] for i,l in enumerate(life) if l==g) for g in [0,1]]
    group_heat=[get('heat_dead_btu_lb'),get('heat_live_btu_lb')]
    wn=[]
    for g in [0,1]:
        bins=[sum(frac[i] for i,l in enumerate(life) if l==g and bin_id(sig[i])==b) for b in range(5)]
        wn.append(sum((bins[bin_id(sig[i])] if bin_id(sig[i]) is not None else 0)*load[i]*(1-.0555)
                      for i,l in enumerate(life) if l==g))
    D=sum(w*math.exp(-138/s) for w,s,l in zip(load,sig,life) if l==0)
    L=sum(w*math.exp(-500/s) for w,s,l in zip(load,sig,life) if l==1)
    mfd=sum(w*math.exp(-138/s)*m for w,s,m,l in zip(load,sig,ms,life) if l==0)/D if D else 0
    mxd=get('mx_dead_fraction')
    mxl=max(mxd,2.9*(D/L)*(1-mfd/mxd)-.226) if L else mxd
    eta_m=[]
    for m,mx in zip(group_m,[mxd,mxl]):
        r=m/mx
        eta_m.append(0 if r>=1 else clamp(1-2.59*r+5.11*r*r-3.52*r*r*r,0,1))
    eta_s=min(1,.174*.01**(-.19))
    density=sum(load)/get('depth_ft')
    beta=density/32
    beta_op=3.348*sigma**(-.8189)
    rb=beta/beta_op
    ag=133*sigma**(-.7913)
    gm=sigma**1.5/(495+.0594*sigma**1.5)
    gamma=gm*math.exp(ag*(math.log(rb)+1-rb))
    ir_group=[gamma*wn[g]*group_heat[g]*eta_m[g]*eta_s for g in [0,1]]
    ir=sum(ir_group)
    sink=density*sum(F[l]*frac[i]*math.exp(-138/sig[i])*(250+1116*ms[i]) for i,l in enumerate(life))
    xi=math.exp((.792+.681*math.sqrt(sigma))*(beta+.1))/(192+.2595*sigma)
    r0=ir*xi/sink
    C=7.47*math.exp(-.133*sigma**.55)
    B=.02526*sigma**.54
    E=.715*math.exp(-.000359*sigma)
    uf=wind_m_s*60/.3048
    pw=C*uf**B*rb**(-E) if uf else 0
    ps=5.275*beta**(-.3)*slope_tangent**2
    pe=pw+ps
    ue_raw=(pe/(C*rb**(-E)))**(1/B) if pe else 0
    ue=min(ue_raw,.9*ir)
    pe_limited=C*ue**B*rb**(-E) if ue else 0
    rh=r0*(1+pe_limited)
    ue_si=ue*.3048/60
    lb=clamp(.936*math.exp(.2566*ue_si)+.461*math.exp(-.1548*ue_si)-.397,1,8)
    e=math.sqrt(max(0,1-1/lb**2))
    back=rh*(1-e)/(1+e)
    a=(rh+back)/2*.3048/60
    b=a/lb
    c=(rh-back)/2*.3048/60
    return dict(fuel=fuel['code'],moisture=list(moisture),wind_m_s=wind_m_s,slope_tangent=slope_tangent,
                cured_fraction=cured,total_load_lb_ft2=sum(load),sigma_ft_inv=sigma,bulk_density_lb_ft3=density,
                beta=beta,beta_op=beta_op,relative_packing=rb,weighted_net_loads_lb_ft2=wn,
                weighted_moistures=group_m,mx_live=mxl,eta_m=eta_m,eta_s=eta_s,
                gamma_min_inv=gamma,ir_groups_btu_ft2_min=ir_group,ir_btu_ft2_min=ir,
                heat_sink_btu_ft3=sink,xi=xi,r0_ft_min=r0,r0_m_s=r0*.3048/60,
                phi_w=pw,phi_s=ps,effective_wind_ft_min_raw=ue_raw,effective_wind_ft_min=ue,
                head_ft_min=rh,head_m_s=rh*.3048/60,lb=lb,eccentricity=e,
                a_m_s=a,b_m_s=b,c_m_s=c)


def hamiltonian(p,d,A,B):
    return dot(d,p)+math.sqrt(dot(A,p)**2+dot(B,p)**2)


def terrain(gx,gy):
    s=math.hypot(gx,gy)
    q=math.sqrt(1+s*s)
    up=(gx/(s*q),gy/(s*q),s/q) if s>1e-12 else (1.,0.,0.)
    side=(-gy/s,gx/s,0.) if s>1e-12 else (0.,1.,0.)
    n=unit((-gx,-gy,1.))
    close(norm(up),1);close(norm(side),1);close(dot(up,side),0)
    close(dot(n,up),0); close(dot(n,side),0)
    return up,side


def main():
    catalog=fuel_catalog()
    cases={}
    for fid in [1,2,4,10]:
        for wind in [0.,1.,3.]:
            key=f"{catalog[fid]['code']}_wind_{wind:g}_flat"
            cases[key]=scalar(catalog[fid],wind_m_s=wind)
    for mlh in [.30,.75,1.20]:
        cases[f'GR1_mlh_{mlh:.2f}']=scalar(catalog[101],(.05,.07,.09,mlh,.90))
    for m in [.12,.20]:
        extinct=scalar(catalog[1],(m,.07,.09,.60,.90))
        close(extinct['ir_btu_ft2_min'],0);close(extinct['r0_m_s'],0)
        cases[f'FM1_extinction_{m:.2f}']=extinct
    close(cases['GR1_mlh_0.30']['cured_fraction'],1)
    close(cases['GR1_mlh_0.75']['cured_fraction'],.5)
    close(cases['GR1_mlh_1.20']['cured_fraction'],0)
    close(.3048/60,.00508)
    terrain(.3,.4)
    up,side=terrain(.5,0)
    R=.1
    d=(0.,0.);A=(R*up[0],R*up[1]);B=(R*side[0],R*side[1])
    east=hamiltonian((1,0),d,A,B);north=hamiltonian((0,1),d,A,B)
    close(east,.1/math.sqrt(1.25));close(north,.1)
    a,b=.2,.1;c=math.sqrt(a*a-b*b)
    H=lambda p:hamiltonian(p,(c,0),(a,0),(0,b))
    close(H((0,1)),.1)
    e=c/a;radial_north=(a+c)*(1-e)
    close(radial_north,.05)
    close(H((2,-4)),2*H((1,-2)))
    close(H((1,0)),a+c);close(H((-1,0)),a-c)
    ax=c+a;ay=b
    close(.4/(ax/10+ay/10),8.452994616207484)
    for row in cases.values():
        close(row['a_m_s']+row['c_m_s'],row['head_m_s'])
        close(row['a_m_s']/row['b_m_s'] if row['b_m_s'] else 1,row['lb'])
    result={
        'notice':'Arithmetic generated from ENGINE_BUILD_PLAN.md, not an independent Behave oracle or solver validation.',
        'behavior_cases':cases,
        'analytic':{
            'circle':{'r0_m':150,'rate_m_s':.1,'time_s':1200,'exact_radius_m':270},
            'ellipse':{'a_m_s':a,'b_m_s':b,'c_m_s':c,'head_m_s':a+c,'back_m_s':a-c,
                       'normal_north_m_s':H((0,1)),'radial_north_m_s':radial_north,
                       'dx_m':10,'dt_cfl_s':.4/(ax/10+ay/10)},
            'terrain_isotropic':{'gx':.5,'gy':0,'surface_rate_m_s':.1,
                                 'east_normal_rate_m_s':east,'north_normal_rate_m_s':north}
        }
    }
    target=ROOT/'fixtures/spec/reference_values.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(f'PASS: arithmetic/analytic checks; wrote {len(cases)} behavior cases to {target}')
    fm1=cases['FM1_wind_0_flat']
    print(f"FM1 R0 = {fm1['r0_ft_min']:.12g} ft/min = {fm1['r0_m_s']:.12g} m/s")


if __name__=='__main__':
    main()
