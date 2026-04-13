import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './tianji.css'
import TianJiView from './TianJiView.jsx'
import AppNav from './AppNav.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppNav />
    <TianJiView />
  </StrictMode>,
)
