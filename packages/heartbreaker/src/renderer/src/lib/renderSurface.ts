// SPDX-License-Identifier: AGPL-3.0-or-later
import { createContext, useContext } from 'react'

export const RenderSurface = createContext({ canvas: false, expanded: false })
export const useRenderSurface = () => useContext(RenderSurface)
