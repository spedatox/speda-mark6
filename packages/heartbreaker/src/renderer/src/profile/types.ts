export interface Brand {
  /** Backend agent this fork talks to — POST /chat/{agentId}, sessions scoped to it. */
  agentId: string
  name: string
  /** Model designation shown beside the name, e.g. "Mark VI". */
  modelNumber: string
  /** First name of the human user — used in the welcome greeting. */
  userName: string
  tagline: string
  avatarInitial: string
  /** The ONE brand colour. theme.ts derives the whole --hb-* palette from it. */
  accent: string
}

/** The runtime profile the UI consumes: a Brand plus the derived hover shade. */
export interface AppProfile extends Brand {
  accentHover: string
}
