/**
 * The shop name, checked here exactly as the server checks it.
 *
 * The rules are the server's -- ``cleanStoreName`` in
 * ``src/services/provisionService.js``: control characters become spaces, runs
 * of whitespace collapse to one, the ends are trimmed, and what is left must be
 * 2 to 60 characters with at least one letter or digit in it.
 *
 * They are repeated here, and again in Pascal in installer/SatpudaCore.iss, on
 * purpose. A name the server will refuse should be refused at the moment it is
 * typed -- in the installer that is before a 200 MB download, and on this screen
 * it is before a round trip that can take a minute. The server still decides;
 * this only means the shopkeeper is told sooner, in the same words.
 */

export const STORE_NAME_MIN = 2
export const STORE_NAME_MAX = 60

/** Collapse to the label the server would store. */
export function cleanStoreName(raw: string): string {
  return String(raw ?? '')
    // Control characters, the ones a pasted name can carry invisibly.
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** "" when the name is usable, otherwise the sentence to show. */
export function storeNameProblem(raw: string): string {
  const name = cleanStoreName(raw)
  if (name.length < STORE_NAME_MIN) {
    return 'Enter the shop name.'
  }
  if (name.length > STORE_NAME_MAX) {
    return `Shop name is too long (maximum ${STORE_NAME_MAX} characters).`
  }
  if (!/[\p{L}\p{N}]/u.test(name)) {
    return 'The shop name needs at least one letter or number.'
  }
  return ''
}
