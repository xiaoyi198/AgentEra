import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './editor.css'
import AgentEditor from './AgentEditor.jsx'
import AppNav from './AppNav.jsx'
import './design-system.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppNav />
    <AgentEditor />
  </StrictMode>,
)
