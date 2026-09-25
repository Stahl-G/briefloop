// Tab rows mark the chosen button with .active; aria-selected gives assistive
// tech the same state, so every row that switches views sets both together.
export function markTab(button,selected){button.classList.toggle('active',selected);button.setAttribute('aria-selected',String(selected))}
