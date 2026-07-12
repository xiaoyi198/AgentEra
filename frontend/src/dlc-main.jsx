import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import DLCStudio from './DLCStudio.jsx'
import AppNav from './AppNav.jsx'
import './AppNav.css'
import './design-system.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppNav />
    <DLCStudio />
  </StrictMode>,
)