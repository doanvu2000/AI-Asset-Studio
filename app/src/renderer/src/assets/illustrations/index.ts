import gsLight from './getting-started-light.webp'
import gsDark from './getting-started-dark.webp'
import libLight from './empty-library-light.webp'
import libDark from './empty-library-dark.webp'
import histLight from './empty-history-light.webp'
import histDark from './empty-history-dark.webp'
import projLight from './empty-projects-light.webp'
import projDark from './empty-projects-dark.webp'

export interface Illustration {
  light: string
  dark: string
  /** Shown size in CSS px; the files are 2x. */
  width: number
  height: number
}

export const ILLUS = {
  gettingStarted: { light: gsLight, dark: gsDark, width: 480, height: 224 },
  emptyLibrary: { light: libLight, dark: libDark, width: 400, height: 300 },
  emptyHistory: { light: histLight, dark: histDark, width: 400, height: 300 },
  emptyProjects: { light: projLight, dark: projDark, width: 400, height: 300 }
} satisfies Record<string, Illustration>
