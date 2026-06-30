import { createRoot } from 'react-dom/client'
import '@renderer/theme/heartbreaker.css'
import Teaser from './Teaser'

// No StrictMode: the teaser is a deterministic timeline; double-invoked effects
// would double-trigger the clock and theme morphs.
createRoot(document.getElementById('root')!).render(<Teaser />)
