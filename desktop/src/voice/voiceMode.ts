/** Is voice driving the screen right now?
 *
 * True while Voice ON (hands-free) is set, and for the moment a spoken command
 * is being carried out. Dropdowns read it so a field a voice command focuses
 * does not pop its suggestion list open: nobody is there to close it by hand.
 * With voice off it is false and the keyboard / mouse behave as always.
 */
let handsFree = false
let busy = 0

export function isVoiceModeOn(): boolean {
  return handsFree || busy > 0
}

/** Voice ON (hands-free listening) itself, without a running command. */
export function isHandsFreeOn(): boolean {
  return handsFree
}

export function setVoiceHandsFree(on: boolean) {
  handsFree = on
}

/** Hands-free listening held paused by something else that uses the mic (the
 *  speaker enrollment wizard). While held the bar does not un-pause the mic
 *  after a spoken reply, does not act on what it hears and F1 does nothing. */
let held = 0

export function isListeningHeld(): boolean {
  return held > 0
}

/** Hold listening paused; call the returned function to let go. */
export function holdListening(): () => void {
  held++
  let done = false
  return () => {
    if (done) return
    done = true
    held = Math.max(0, held - 1)
  }
}

/** Mark a voice command as running; call the returned function when it is done. */
export function voiceBusy(): () => void {
  busy++
  let done = false
  return () => {
    if (done) return
    done = true
    busy = Math.max(0, busy - 1)
  }
}
