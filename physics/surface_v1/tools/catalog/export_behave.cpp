// USFS Behave catalog export; source commit is recorded in README.md.
#include "fuelModels.h"
#include <iostream>
#include <iomanip>
int main(){
 FuelModels f; std::cout<<std::setprecision(17);
 std::cout<<"fuel_id,code,name,is_dynamic,depth_ft,mx_dead,heat_dead_btu_lb,heat_live_btu_lb,w1h_lb_ft2,w10h_lb_ft2,w100h_lb_ft2,wlh_lb_ft2,wlw_lb_ft2,sav1h_per_ft,savlh_per_ft,savlw_per_ft,rho_p_lb_ft3,s_t,s_e,sav10h_per_ft,sav100h_per_ft\n";
 for(int id=101;id<=204;id++){
  if(!((id>=101&&id<=109)||(id>=121&&id<=124)||(id>=141&&id<=149)||(id>=161&&id<=165)||(id>=181&&id<=189)||(id>=201&&id<=204)))continue;
  std::cout<<id<<","<<f.getFuelCode(id)<<",\""<<f.getFuelName(id)<<"\","<<f.getIsDynamic(id)<<","<<f.getFuelbedDepth(id,LengthUnits::Feet)<<","<<f.getMoistureOfExtinctionDead(id,FractionUnits::Fraction)<<","<<f.getHeatOfCombustionDead(id,HeatOfCombustionUnits::BtusPerPound)<<","<<f.getHeatOfCombustionLive(id,HeatOfCombustionUnits::BtusPerPound)<<","<<f.getFuelLoadOneHour(id,LoadingUnits::PoundsPerSquareFoot)<<","<<f.getFuelLoadTenHour(id,LoadingUnits::PoundsPerSquareFoot)<<","<<f.getFuelLoadHundredHour(id,LoadingUnits::PoundsPerSquareFoot)<<","<<f.getFuelLoadLiveHerbaceous(id,LoadingUnits::PoundsPerSquareFoot)<<","<<f.getFuelLoadLiveWoody(id,LoadingUnits::PoundsPerSquareFoot)<<","<<f.getSavrOneHour(id,SurfaceAreaToVolumeUnits::SquareFeetOverCubicFeet)<<","<<f.getSavrLiveHerbaceous(id,SurfaceAreaToVolumeUnits::SquareFeetOverCubicFeet)<<","<<f.getSavrLiveWoody(id,SurfaceAreaToVolumeUnits::SquareFeetOverCubicFeet)<<",32,0.0555,0.01,109,30\n";
 }
}
