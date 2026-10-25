import { Link } from 'react-router-dom'
import FpsMeter from './FpsMeter'

export default function PrototypeChrome({ pattern, title, blurb, children, footer }) {
  return (
    <div className="proto">
      <header className="proto__bar">
        <div className="proto__bar-left">
          <Link to="/compare" className="proto__back">
            ← Compare
          </Link>
          <span className="proto__pattern">{pattern}</span>
          <h1 className="proto__title">{title}</h1>
        </div>
        <FpsMeter />
      </header>
      {blurb ? <p className="proto__blurb">{blurb}</p> : null}
      <div className="proto__stage">{children}</div>
      {footer ? <footer className="proto__footer">{footer}</footer> : null}
    </div>
  )
}
