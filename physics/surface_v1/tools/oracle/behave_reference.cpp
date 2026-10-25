// Independent oracle: compile against the pinned USFS Behave source, never Rust output.
#include "surface.h"
#include "fuelModels.h"
#include <iostream>
#include <iomanip>
int main() {
  FuelModels fuels;
  Surface surface(fuels);
  std::cout << std::setprecision(17) << "[\n";
  bool first = true;
  for (int fuel : {1, 2, 4, 10, 101}) {
    for (double mlh : {0.30, 0.60, 1.20, 1.21}) {
      if ((fuel == 101 && mlh == 0.60) || (fuel != 101 && mlh != 0.60)) continue;
      for (double slope : {0.0, 0.30}) {
    for (double wind : {0.0, 1.0, 3.0}) {
      surface.updateSurfaceInputs(fuel, .05, .07, .09, mlh, .90,
        FractionUnits::Fraction, wind, SpeedUnits::MetersPerSecond,
        WindHeightInputMode::DirectMidflame, 0.0,
        WindAndSpreadOrientationMode::RelativeToUpslope, slope * 100.0, SlopeUnits::Percent,
        0.0, 0.0, FractionUnits::Fraction, 0.0, LengthUnits::Feet,
        0.0, FractionUnits::Fraction);
      surface.doSurfaceRunInDirectionOfMaxSpread();
      if (!first) std::cout << ",\n";
      first = false;
      std::cout << "{\"fuel\":" << fuel << ",\"wind_m_s\":" << wind
        << ",\"mlh\":" << mlh << ",\"slope\":" << slope
        << ",\"r_head_m_s\":" << surface.getSpreadRate(SpeedUnits::MetersPerSecond)
        << ",\"ir\":" << surface.getReactionIntensity(HeatSourceAndReactionIntensityUnits::BtusPerSquareFootPerMinute)
        << ",\"h_sink\":" << surface.getHeatSink(HeatSinkUnits::BtusPerCubicFoot)
        << ",\"beta\":" << surface.getPackingRatio() << "}";
    }
  }
    }
    }
  std::cout << "\n]\n";
}
