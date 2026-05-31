export interface AppProfile {
  name: string
  /** Model designation shown beside the name, e.g. "Mark VI" */
  modelNumber: string
  /** First name of the human user — used in the welcome greeting */
  userName: string
  tagline: string
  avatarInitial: string
  accent: string
  accentHover: string
  /** Suggested prompts shown on the welcome screen */
  suggestedPrompts: string[]
}
