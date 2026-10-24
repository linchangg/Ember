export default function TimeSlider({ t, onChange }) {
  return (
    <div className="time-slider">
      <label htmlFor="tslider">Scroll through time</label>
      <input
        id="tslider"
        type="range"
        min={-360}
        max={360}
        step={5}
        value={t}
        onChange={(e) => onChange(+e.target.value)}
      />
      <div className="time-slider__ticks">
        <span>08:05 · ignition</span>
        <span>11:05</span>
        <span className="time-slider__now">14:05 · now</span>
        <span>17:05</span>
        <span>20:05</span>
      </div>
    </div>
  )
}
