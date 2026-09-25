/**
 * The real sign-in screen, arriving filled in.
 *
 * The demo opens here, as the product does for anyone not signed in, with
 * the demo's credentials already in the two fields — one click, and any other
 * input works as well. The page itself
 * is untouched: this wraps it and types into its inputs the way a person or a
 * password manager would, through the native value setter and an `input`
 * event, which is what React's `onChange` listens for.
 */
import { useEffect } from 'react'
import LoginPage from '../../src/pages/LoginPage'
import { DEMO_CREDENTIALS } from '../mock/fixtures/world'

type Props = Parameters<typeof LoginPage>[0]

function fill(input: HTMLInputElement | null, value: string): void {
  if (!input || input.value) return
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

export default function DemoLoginPage(props: Props) {
  useEffect(() => {
    const form = document.querySelector<HTMLFormElement>('form.rm-auth-card')
    fill(form?.querySelector<HTMLInputElement>('input[type="email"], #email') ?? null, DEMO_CREDENTIALS.email)
    fill(form?.querySelector<HTMLInputElement>('input[type="password"]') ?? null, DEMO_CREDENTIALS.password)
  }, [])
  return <LoginPage {...props} />
}
