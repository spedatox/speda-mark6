function artifactControls(body) {
  const groups = [
    'input[type=range], [role=slider]',
    'input:not([type]), input:not([type=hidden],[type=range],[type=radio],[type=checkbox],[type=button],[type=submit],[type=reset],[type=image]), textarea, [contenteditable=true]',
    'select, input[type=radio], input[type=checkbox], [role=checkbox], [role=radio], [role=switch]',
    'button, input[type=button], input[type=submit], input[type=reset], [role=button]',
    'summary',
  ]
  return groups.reduce((mask, selector, index) => {
    const visible = Array.from(body.querySelectorAll(selector)).some(control =>
      !control.matches(':disabled, [aria-disabled=true]') &&
      control.getClientRects().length > 0 && getComputedStyle(control).visibility === 'visible'
    )
    return visible ? mask | (1 << index) : mask
  }, 0)
}
