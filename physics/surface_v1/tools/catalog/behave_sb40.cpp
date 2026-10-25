#include "surface.h"
#include "fuelModels.h"
#include <iostream>
#include <iomanip>
int main(){FuelModels fuels;Surface surface(fuels);bool first=true;std::cout<<std::setprecision(17)<<"[\n";
for(int id=101;id<=204;id++){
 if(!((id>=101&&id<=109)||(id>=121&&id<=124)||(id>=141&&id<=149)||(id>=161&&id<=165)||(id>=181&&id<=189)||(id>=201&&id<=204)))continue;
 for(double wind:{0.0,3.0}){
 surface.updateSurfaceInputs(id,.03,.04,.05,.30,.60,FractionUnits::Fraction,wind,SpeedUnits::MetersPerSecond,WindHeightInputMode::DirectMidflame,0.0,WindAndSpreadOrientationMode::RelativeToUpslope,30.0,SlopeUnits::Percent,0.0,0.0,FractionUnits::Fraction,0.0,LengthUnits::Feet,0.0,FractionUnits::Fraction);
 surface.doSurfaceRunInDirectionOfMaxSpread();if(!first)std::cout<<",\n";first=false;
 std::cout<<"{\"fuel\":"<<id<<",\"wind_m_s\":"<<wind<<",\"r_head_m_s\":"<<surface.getSpreadRate(SpeedUnits::MetersPerSecond)<<",\"ir\":"<<surface.getReactionIntensity(HeatSourceAndReactionIntensityUnits::BtusPerSquareFootPerMinute)<<",\"h_sink\":"<<surface.getHeatSink(HeatSinkUnits::BtusPerCubicFoot)<<",\"beta\":"<<surface.getPackingRatio()<<"}";
 }}std::cout<<"\n]\n";}
