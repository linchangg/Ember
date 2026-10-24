import { useState } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import TerrainMap from './components/TerrainMap'
import TimeSlider from './components/TimeSlider'
import Sidebar from './components/Sidebar'
import Compare from './pages/Compare'
import PatternCesium from './pages/PatternCesium'
import PatternMapLibreThree from './pages/PatternMapLibreThree'
import PatternHybrid from './pages/PatternHybrid'
import './App.css'

function Dashboard() {
  const [t, setT] = useState(150)

  return (
    <div className="app">
      <header className="header">
        <div className="header__left">
          <Link to="/" className="header__brand">
            Emberline
          </Link>
          <span className="header__incident">Sierra Vista Fire</span>
          <span className="header__meta">Mock scenario · synthetic terrain</span>
        </div>
        <div className="header__right">
          <Link to="/compare" className="header__link">
            Globe bake-off
          </Link>
          <span className="header__update">
            Last satellite update 14:03 · forecast refreshes every 5 min
          </span>
        </div>
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

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/compare" element={<Compare />} />
      <Route path="/compare/cesium" element={<PatternCesium />} />
      <Route path="/compare/maplibre-three" element={<PatternMapLibreThree />} />
      <Route path="/compare/hybrid" element={<PatternHybrid />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
