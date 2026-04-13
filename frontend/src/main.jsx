import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import AppNav from './AppNav.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppNav />
    <App />
  </StrictMode>,
)
