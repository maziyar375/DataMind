/**
 * The mock client exports everything the real one does, each with a type the
 * app can use in its place.
 *
 * Checked by `tsc -p demo/tsconfig.json` and never imported at runtime. If the
 * real client grows an export — a namespace, a helper, a method on an existing
 * namespace — and the mock does not, this assignment stops compiling, which is
 * the moment to add it rather than the moment a screen in the published demo
 * goes blank.
 */
import type * as Real from '../../src/api/client'
import type * as Mock from './client'

type Surface<T> = { [K in keyof T]: T[K] }

export const surfaceIsComplete = (mock: Surface<typeof Mock>): Surface<typeof Real> => mock
