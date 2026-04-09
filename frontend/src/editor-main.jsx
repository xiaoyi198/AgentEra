import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './editor.css'
import AgentEditor from './AgentEditor.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AgentEditor />
  </StrictMode>,
)
