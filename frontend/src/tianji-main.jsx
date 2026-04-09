import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './tianji.css'
import TianJiView from './TianJiView.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <TianJiView />
  </StrictMode>,
)
