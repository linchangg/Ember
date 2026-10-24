import { useState } from 'react'
import TerrainMap from './components/TerrainMap'
import TimeSlider from './components/TimeSlider'
import Sidebar from './components/Sidebar'
import './App.css'

export default function App() {
  const [t, setT] = useState(150)

  return (
    <div className="app">
      <header className="header">
        <div className="header__left">
          <span className="header__brand">Emberline</span>
          <span className="header__incident">Sierra Vista Fire</span>
          <span className="header__meta">Mock scenario · synthetic terrain</span>
        </div>
        <span className="header__update">
          Last satellite update 14:03 · forecast refreshes every 5 min
        </span>
      </header>

      <div className="layout">
        <main className="layout__main">
          <TerrainMap t={t} />
          <TimeSlider t={t} onChange={setT} />
        </main>
        <Sidebar t={t} />
      </div>
    </div>
  )
}
