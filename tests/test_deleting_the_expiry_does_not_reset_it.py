"""Deleting the licence file must not buy anything.

THE OWNER'S DECISION, in his words: once expiry is set at first activation --
Online or Offline -- it must not change except from the server or from the
software's own Administrator settings. Deleting the file and reinstalling must
NOT reset it: *"delete kelch tr aapn internet suru karnyas sangu shakto mhanaje
tech file punha yeil"* -- if they delete it we can tell them to turn the internet
on, and the same file comes back.

WHAT THE OLD BUILD DID, and every one of these is a test below that fails on it:

  * ``check_expiry()`` read ``expiry.dat``, found nothing, and returned False.
    Deleting one file in AppData was a permanent licence.
  * ``expiry_config.json`` is plain JSON in the same folder.
    ``{"apply_expiry_check": false}`` typed into Notepad switched the whole
    mechanism off.
  * A wiped AppData plus the installer produced ``_activate_offline_locally``,
    which activates with no expiry at all, because the server refuses a second
    trial to a computer that has already had one and the refusal was read as
    permission.
  * ``save_expiry_settings`` wrote a local file when Offline, so a shop with the
    network unplugged could set its own expiry date.

WHAT REPLACES IT. The server signs the expiry; the desktop verifies the signature
and the machine binding before believing it; a blob that is missing or does not
verify is not a licence and is not permission either -- the shop is asked to
connect the internet once, and the same signed expiry comes back.

The blobs in tests/data/license_seal_blobs.json were signed by the SERVER's own
Node module, not by Python, so what is verified here is what actually reaches a
shop.
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import license_manager as lm  # noqa: E402
from core import license_seal as seal  # noqa: E402
from core import trial_activation as ta  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                       "license_seal_blobs.json")

# The hardware the fixture blobs were bound to. sha256 of each of these is the
# 'a'*64 / 'b'*64 ... the signing probe used, so a machine reporting these parts
# IS the machine those blobs belong to.
_PARTS_FOR_FIXTURE = {
    "mac": "1", "cpu": "CPU-A", "board": "BOARD-B", "sysuuid": "UUID-C", "disk": "DISK-D",
}


def _load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class SealHarness(unittest.TestCase):
    """A temporary AppData, a known machine, and no network unless asked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.appdata = self._tmp.name
        self.parts = dict(_PARTS_FOR_FIXTURE)
        self._patches = [
            mock.patch.object(lm, "_appdata_dir", lambda: self.appdata),
            mock.patch.object(lm, "_hardware_parts", lambda: dict(self.parts)),
            mock.patch.object(lm, "_PARTS_MEMO", {}),
            # the "has this computer sold anything" answer is cached per process
            mock.patch.dict(seal._TRADING_CACHE, {}, clear=True),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._stop)
        # A throwaway key, installed into the build's key set for the duration of
        # the test, so blobs can be signed for THIS machine. The fixture blobs
        # from the real server are used separately, for the signature itself.
        self.kid, pub_b64, self.sign = _test_signer()
        self._keys = mock.patch.dict(seal._PUBLIC_KEYS, {self.kid: pub_b64})
        self._keys.start()
        seal._LAST_FETCH_AT = 0.0
        lm._REAUTH_FORCED = False

    def _stop(self):
        for p in reversed(self._patches):
            p.stop()
        try:
            self._keys.stop()
        except Exception:
            pass
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------------
    def machine_binding(self):
        return seal.binding_digests()

    def make_blob(self, **over):
        payload = {
            "v": 1, "alg": "RS256", "kid": self.kid,
            "sid": "trial_0c4e19b7aa31",
            "act": "2026-09-09",
            "exp": str(date.today() + timedelta(days=30)),
            "een": True, "aec": True, "act_ok": True,
            "hw": "hw-0123456789abcdef",
            "dev": "pc-0123456789abcdef",
            "hwp": self.machine_binding(),
            "iat": 1789000000, "ser": 1789012800,
        }
        payload.update(over)
        return self.sign(payload)

    def write_activation(self, day: str):
        record = {"hw": lm._get_hardware_hash(), "date": day,
                  "hw_parts": dict(self.parts)}
        with open(lm._activation_path(), "wb") as fh:
            fh.write(lm._encrypt(record))


def _test_signer():
    """A throwaway RSA key, and a function that signs a payload the way Node does."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import hashlib

    kid = hashlib.sha256(der).hexdigest()[:16]
    pub_b64 = base64.b64encode(der).decode()

    def _b64u(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def sign(payload: dict) -> str:
        payload = dict(payload)
        payload["kid"] = kid
        head = f"{seal.SEAL_PREFIX}.{_b64u(json.dumps(payload, separators=(',', ':')).encode())}"
        sig = key.sign(head.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{_b64u(sig)}"

    return kid, pub_b64, sign


class TheServersSignatureIsWhatTheBuildChecks(SealHarness):
    """Blobs signed by the real Node module verify here — and only those."""

    def test_a_blob_signed_by_the_server_verifies_in_the_build(self):
        fx = _load_fixture()
        self.assertIn(
            fx["kid"], seal._PUBLIC_KEYS,
            "the build no longer carries the key the fixture was signed with; "
            "regenerate tests/data/license_seal_blobs.json alongside the key",
        )
        payload = seal.verify_blob(fx["blobs"]["good"])
        self.assertIsNotNone(payload, "a genuine server blob failed verification")
        self.assertEqual(payload["exp"], "2026-09-12")
        self.assertEqual(payload["sid"], "trial_0c4e19b7aa31")

    def test_both_verifiers_agree_on_every_fixture_blob(self):
        """``cryptography`` and the standard-library fallback, same answers.

        The fallback is not decoration: a frozen build where that import fails is
        a case this code base already carries a workaround for. If the two ever
        disagree, one of them is wrong and a shop gets the wrong answer depending
        on how its exe was built.
        """
        fx = _load_fixture()
        spki = base64.b64decode(seal._PUBLIC_KEYS[fx["kid"]])
        for name, blob in fx["blobs"].items():
            head, sig_b64 = blob.rsplit(".", 1)
            msg = head.encode("ascii")
            sig = seal._b64url_decode(sig_b64)
            with self.subTest(blob=name):
                self.assertTrue(seal._verify_stdlib(spki, msg, sig))
                self.assertIs(seal._verify_cryptography(spki, msg, sig), True)
                # And both reject the same corruption.
                bad = bytearray(sig)
                bad[7] ^= 0x01
                self.assertFalse(seal._verify_stdlib(spki, msg, bytes(bad)))
                self.assertIs(seal._verify_cryptography(spki, msg, bytes(bad)), False)

    def test_an_edited_date_breaks_the_signature(self):
        """The point of signing it. Editing the date is not a licence change."""
        blob = self.make_blob(exp="2099-01-01")
        self.assertIsNotNone(seal.verify_blob(blob))
        head, sig = blob.rsplit(".", 1)
        prefix, body = head.split(".", 1)
        payload = json.loads(seal._b64url_decode(body))
        payload["exp"] = "2099-12-31"
        forged_body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        forged = f"{prefix}.{forged_body}.{sig}"
        self.assertIsNone(
            seal.verify_blob(forged),
            "an edited expiry date was accepted -- the signature is not being checked",
        )

    def test_a_signature_from_an_unknown_key_is_refused(self):
        """A key the build does not carry cannot mint a licence for it."""
        other_kid, _pub, other_sign = _test_signer()
        blob = other_sign({
            "v": 1, "alg": "RS256", "kid": other_kid, "sid": "x",
            "exp": "2099-01-01", "een": True, "aec": True, "act_ok": True,
            "hwp": self.machine_binding(), "ser": 1,
        })
        # Not installed into _PUBLIC_KEYS for this test.
        with mock.patch.dict(seal._PUBLIC_KEYS, {}, clear=False):
            seal._PUBLIC_KEYS.pop(other_kid, None)
            self.assertIsNone(seal.verify_blob(blob))


class ABlobBelongsToOneComputer(SealHarness):
    def test_a_blob_from_another_machine_is_refused(self):
        """Copying the file to a second PC is the obvious next idea. It fails."""
        blob = self.make_blob()
        self.assertIsNotNone(seal.store_seal(blob, fresh=True))
        # Same blob, different computer: every component differs.
        self.parts.update(
            {"cpu": "OTHER-CPU", "board": "OTHER-BOARD",
             "sysuuid": "OTHER-UUID", "disk": "OTHER-DISK"}
        )
        payload, state = seal.local_payload()
        self.assertIsNone(payload)
        self.assertEqual(state, seal.STATE_TAMPERED)

    def test_one_component_may_change_without_losing_the_licence(self):
        """A replaced disk is not a different shop.

        This code base has been bitten repeatedly by a fingerprint that moved
        when Windows installed a virtual adapter or a WMIC call timed out, and a
        shop locked out of its own till by a disk swap is worse than a licence
        one component looser. Two components differing is still a different PC.
        """
        blob = self.make_blob()
        self.assertIsNotNone(seal.store_seal(blob, fresh=True))
        self.parts["disk"] = "REPLACED-DISK"
        payload, state = seal.local_payload()
        self.assertEqual(state, seal.STATE_SIGNED, "a disk swap must not void the licence")
        self.parts["board"] = "REPLACED-BOARD"
        payload, state = seal.local_payload()
        self.assertEqual(state, seal.STATE_TAMPERED, "two components changed is another PC")


class DeletingTheFileBuysNothing(SealHarness):
    def setUp(self):
        super().setUp()
        # A shop activated AFTER the seal existed: no upgrade allowance.
        self.write_activation(str(date.today()))

    def test_no_licence_blocks_instead_of_granting_free_use(self):
        """THE test. On the old build this returned False -- unlimited use.

        The premise is in the file's name: a PC that HAS held a licence and no
        longer has the file. That is recorded inside the encrypted
        activation.dat when the blob is accepted, so deleting license.seal --
        or the whole folder and reinstalling -- cannot turn this PC back into a
        fresh install with a starter window to spend.
        """
        seal.store_seal(self.make_blob(), fresh=True)
        os.remove(seal.seal_path())
        self.assertFalse(os.path.exists(seal.seal_path()))
        self.assertTrue(seal.seal_ever_stored())
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(
                lm.check_expiry(),
                "a missing licence was read as 'no restriction' -- the old bug",
            )
            self.assertEqual(lm.expiry_block_reason(), "needs_internet")

    def test_deleting_the_licence_does_not_buy_a_starter_window(self):
        """The fresh-install window is for a PC that never had a licence.

        Without the "this PC has held one" mark, deleting license.seal would
        land in STATE_MISSING with an activation dated inside the window, and
        the delete would pay for three more days every time.
        """
        seal.store_seal(self.make_blob(), fresh=True)
        os.remove(seal.seal_path())
        self.assertIsNone(seal.fresh_install_window())
        with mock.patch.object(seal, "fetch_seal", lambda **k: None):
            st = seal.seal_state(fetch=False)
        self.assertEqual(st["state"], seal.STATE_MISSING)
        self.assertTrue(st["blocked"])
        self.assertEqual(st["reason"], "needs_internet")

    def test_a_missing_licence_does_not_wipe_activation(self):
        """It must not force re-activation.

        An Offline expiry deletes activation.dat on purpose, to make the vendor
        re-activate. Doing that because a file went missing destroys the one
        record saying this PC was ever activated, and turns a one-connection
        problem into a support call.
        """
        seal.store_seal(self.make_blob(), fresh=True)
        os.remove(seal.seal_path())
        act = lm._activation_path()
        self.assertTrue(os.path.exists(act))
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            lm._REAUTH_FORCED = False
            self.assertTrue(lm.check_expiry())
        self.assertTrue(
            os.path.exists(act),
            "activation.dat was deleted because the licence file was missing",
        )

    def test_deleting_it_fetches_the_same_expiry_back(self):
        """"tech file punha yeil" -- the same file comes back, not a new one."""
        original = str(date.today() - timedelta(days=4))
        blob = self.make_blob(exp=original)
        seal.store_seal(blob, fresh=True)
        self.assertEqual(seal.local_payload()[1], seal.STATE_SIGNED)

        os.remove(seal.seal_path())
        self.assertEqual(seal.local_payload()[1], seal.STATE_MISSING)

        # The server remembers this computer and re-issues the SAME date.
        served = {"signed": self.make_blob(exp=original)}
        rec = mock.MagicMock(return_value=served)
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch("core.server_api.store_token_for_active", lambda *a, **k: ""), \
             mock.patch("core.server_api.recover_signed_license", rec):
            seal._LAST_FETCH_AT = 0.0
            self.assertTrue(lm.check_expiry(), "the recovered licence had already lapsed")
            self.assertTrue(rec.called, "no attempt was made to fetch the licence back")
        payload, state = seal.local_payload()
        self.assertEqual(state, seal.STATE_SIGNED)
        self.assertEqual(payload["exp"], original, "a fresh expiry was minted instead")

    def test_the_recovery_call_sends_the_hardware_and_no_credential(self):
        """It has to work with nothing but the machine — the key is gone too."""
        seen = {}

        def _recover(body, **kw):
            seen.update(body or {})
            return {"signed": self.make_blob()}

        with mock.patch("core.server_api.store_token_for_active", lambda *a, **k: ""), \
             mock.patch("core.server_api.recover_signed_license", _recover):
            seal._LAST_FETCH_AT = 0.0
            self.assertIsNotNone(seal.fetch_seal(force=True))
        self.assertTrue(seen.get("machine_id"), "the fingerprint was not sent")
        self.assertEqual(set(seen), {"machine_id", "device_id", "hw_parts"})

    def test_turning_the_check_off_in_a_text_file_no_longer_works(self):
        """``expiry_config.json`` is plain JSON in the shop's own folder.

        ``{"apply_expiry_check": false}`` used to switch the entire expiry
        mechanism off. The flag now travels inside the signed blob.
        """
        lm.write_expiry_config(False)
        self.assertFalse(lm.is_expiry_check_applied())
        seal.store_seal(self.make_blob(exp=str(date.today() - timedelta(days=1))),
                        fresh=True)
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(
                lm.check_expiry(),
                "a JSON file in AppData switched off a signed expiry",
            )

    def test_a_stale_blob_cannot_be_replayed_over_a_shortened_one(self):
        """Keeping last year's generous blob and putting it back must not work."""
        generous = self.make_blob(exp="2099-01-01", ser=1_000)
        shortened = self.make_blob(exp=str(date.today() - timedelta(days=1)), ser=2_000)
        seal.store_seal(generous, fresh=True)
        seal.store_seal(shortened, fresh=True)      # ratchet moves to 2000
        with open(seal.seal_path(), "w", encoding="utf-8") as fh:
            fh.write(generous)                       # the replay
        payload, state = seal.local_payload()
        self.assertIsNone(payload)
        self.assertEqual(state, seal.STATE_TAMPERED)


class ReinstallingDoesNotStartOver(SealHarness):
    def test_the_offline_fallback_recovers_instead_of_granting_free_use(self):
        """The wiped-AppData-plus-installer route.

        The server refuses a second trial to a computer that has already had one.
        The old code read that refusal as "activate locally, with no expiry",
        which handed out an unlimited licence as the reward for deleting a
        folder. It must now come back with the licence this machine already has.
        """
        lapsed = str(date.today() - timedelta(days=9))
        served = {"signed": self.make_blob(exp=lapsed, act="2026-08-01")}
        calls = {"n": 0}

        def _recover(body, **kw):
            calls["n"] += 1
            return served

        with mock.patch("core.store_manager.setup_initial_store_on_activation",
                        lambda name: None), \
             mock.patch("core.sync_prefs.set_sync_mode", lambda mode: None), \
             mock.patch("core.server_api.store_token_for_active", lambda *a, **k: ""), \
             mock.patch("core.server_api.recover_signed_license", _recover):
            seal._LAST_FETCH_AT = 0.0
            result = ta._activate_offline_locally("Roshan Medical", "no internet")

        self.assertTrue(result["ok"])
        self.assertEqual(calls["n"], 1, "the licence was not asked for at all")
        self.assertEqual(
            result["expiry_date"], lapsed,
            "the reinstall was given a clean slate instead of its own expiry",
        )
        payload, state = seal.local_payload()
        self.assertEqual(state, seal.STATE_SIGNED)
        self.assertEqual(payload["exp"], lapsed)

    def test_a_wiped_appdata_gets_no_upgrade_allowance(self):
        """Grace is for shops that were already running, not for a fresh wipe.

        A wipe takes activation.dat with it, so there is no record claiming this
        PC predates the seal — and the delete trick therefore stops paying today
        rather than when the grace window closes.
        """
        for name in os.listdir(self.appdata):
            os.remove(os.path.join(self.appdata, name))
        self.assertFalse(seal.in_legacy_grace())
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(lm.check_expiry())


class ShopsAlreadyRunningKeepRunning(SealHarness):
    """The upgrade path. Nothing below may change on the day a shop updates."""

    def test_a_pre_seal_shop_with_a_legacy_expiry_is_untouched(self):
        self.write_activation("2026-05-01")          # before SEAL_CUTOVER
        lm.write_expiry(str(date.today() + timedelta(days=200)), enabled=True)
        self.assertTrue(seal.in_legacy_grace())
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertFalse(lm.check_expiry(), "an existing shop was blocked by the upgrade")

    def test_a_pre_seal_shop_whose_legacy_date_has_passed_still_expires(self):
        self.write_activation("2026-05-01")
        lm.write_expiry(str(date.today() - timedelta(days=1)), enabled=True)
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(lm.check_expiry())

    def test_grace_ends_on_a_fixed_date_a_delete_cannot_move(self):
        """The window is an absolute date compiled into the build.

        A "first run plus N days" counter would live in AppData, and a counter a
        delete resets is the very hole being closed.
        """
        self.write_activation("2026-05-01")
        self.assertTrue(seal.in_legacy_grace())
        after = date.fromisoformat(seal.LEGACY_GRACE_UNTIL) + timedelta(days=1)
        with mock.patch.object(seal, "_today", lambda: after):
            self.assertFalse(seal.in_legacy_grace())
            with mock.patch.object(lm, "_is_online_mode", lambda: False), \
                 mock.patch.object(seal, "fetch_seal", lambda **k: None):
                self.assertTrue(lm.check_expiry())

    def test_the_seal_overrides_a_legacy_file_once_it_arrives(self):
        """A signed licence beats the old file, in both directions."""
        self.write_activation("2026-05-01")
        lm.write_expiry(str(date.today() + timedelta(days=200)), enabled=True)
        seal.store_seal(self.make_blob(exp=str(date.today() - timedelta(days=1))),
                        fresh=True)
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(
                lm.check_expiry(),
                "a generous legacy file overrode the signed expiry",
            )


class OnlyTheServerMovesTheDate(SealHarness):
    """Administrator settings: internet required, both records or neither."""

    def setUp(self):
        super().setUp()
        self.write_activation(str(date.today()))

    def test_no_credentials_changes_nothing(self):
        before = seal.read_seal()
        out = lm.save_expiry_settings(enabled=True, expiry_date="2099-01-01")
        self.assertFalse(out["ok"])
        self.assertIn("administrator", out["error"].lower())
        self.assertEqual(seal.read_seal(), before)

    def test_offline_only_saving_is_refused_outright(self):
        """There is no local-only path left, in either mode."""
        out = lm.save_expiry_settings(
            enabled=True, expiry_date="2099-01-01", push_remote=False,
        )
        self.assertFalse(out["ok"])
        self.assertIn("server", out["error"].lower())

    def test_an_unreachable_server_changes_nothing_at_all(self):
        seal.store_seal(self.make_blob(exp="2026-09-12"), fresh=True)
        before = seal.read_seal()
        with mock.patch("core.server_api.admin_login",
                        side_effect=RuntimeError("Cannot reach server")):
            out = lm.save_expiry_settings(
                enabled=True, expiry_date="2099-01-01",
                admin_username="admin", admin_password="pw",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(seal.read_seal(), before, "the local licence moved anyway")

    def test_a_successful_save_writes_the_server_record_and_the_blob_together(self):
        new_date = str(date.today() + timedelta(days=365))
        sent = {}

        def _set(token, store_id, body, **kw):
            sent["token"] = token
            sent["store_id"] = store_id
            sent["body"] = dict(body)
            return {
                "license": {
                    "expiry_date": new_date, "expiry_enabled": True,
                    "apply_expiry_check": True, "activation_date": "2026-09-09",
                    "is_active": True, "access_allowed": True,
                },
                "signed": self.make_blob(exp=new_date, ser=9_999_999),
            }

        with mock.patch("core.server_api.admin_login", lambda u, p: "ADMIN-TOKEN"), \
             mock.patch.object(lm, "_resolve_remote_store_id",
                               lambda t: "trial_0c4e19b7aa31"), \
             mock.patch("core.server_api.admin_set_license", _set), \
             mock.patch.object(lm, "_is_online_mode", lambda: False):
            out = lm.save_expiry_settings(
                enabled=True, expiry_date=new_date,
                admin_username="admin", admin_password="pw",
            )

        self.assertTrue(out["ok"], out.get("error"))
        self.assertTrue(out["sealed"], "the signed licence was not stored")
        self.assertEqual(sent["token"], "ADMIN-TOKEN")
        self.assertEqual(sent["body"]["expiry_date"], new_date)
        # The binding travels with the edit, so the blob comes back usable here.
        self.assertIn("machine_id", sent["body"])
        self.assertIn("hw_parts", sent["body"])
        payload, state = seal.local_payload()
        self.assertEqual(state, seal.STATE_SIGNED)
        self.assertEqual(payload["exp"], new_date)
        # And the unsigned mirror agrees, so no screen shows a different date.
        self.assertEqual(lm._read_expiry().get("expiry_date"), new_date)

    def test_a_blob_that_will_not_verify_here_is_reported_not_stored(self):
        """The server saved it but this build cannot check it. Say so."""
        other_kid, _pub, other_sign = _test_signer()
        foreign = other_sign({
            "v": 1, "alg": "RS256", "kid": other_kid, "sid": "x",
            "exp": "2099-01-01", "een": True, "aec": True, "act_ok": True,
            "hwp": self.machine_binding(), "ser": 5,
        })
        seal._PUBLIC_KEYS.pop(other_kid, None)
        with mock.patch("core.server_api.admin_login", lambda u, p: "T"), \
             mock.patch.object(lm, "_resolve_remote_store_id", lambda t: "s"), \
             mock.patch("core.server_api.admin_set_license",
                        lambda *a, **k: {"license": {"expiry_date": "2099-01-01"},
                                         "signed": foreign}):
            out = lm.save_expiry_settings(
                enabled=True, expiry_date="2099-01-01",
                admin_username="admin", admin_password="pw",
            )
        self.assertFalse(out["ok"])
        self.assertIn("verify", out["error"].lower())
        self.assertEqual(seal.read_seal(), "")


class AFreshInstallCanOpenTheTill(SealHarness):
    """The bug the owner saw: activated, told it worked, then blocked forever.

    A shop signing up Offline with no internet -- or with a server that cannot
    answer, which on live is EVERY sign-up, because POST /api/provision/trial
    has returned 500 since the feature shipped -- writes activation.dat, gets no
    signed licence, and the very next launch lands on a screen whose heading is
    "Licence not found". Connecting the internet did not help either: the
    recovery endpoint can only answer for a machine the trial call recorded, and
    the trial call never succeeded.

    So a PC that has just activated and has NEVER held a licence runs on the
    same window the server would have given it, and then expires like any other
    trial. Everything below is about keeping that window from becoming a way to
    get free time.
    """

    def setUp(self):
        super().setUp()
        self.write_activation(str(date.today()))

    def _blocked(self):
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            return lm.check_expiry()

    def _reason(self):
        with mock.patch.object(seal, "fetch_seal", lambda **k: None):
            return lm.expiry_block_reason()

    def test_the_till_opens_on_the_day_it_was_set_up(self):
        """The one assertion that would have caught this before it shipped."""
        self.assertFalse(self._blocked(), "a fresh install was blocked on day 0")

    def test_it_stays_open_for_the_whole_window(self):
        for day in range(seal.FRESH_INSTALL_DAYS):
            self.write_activation(str(date.today() - timedelta(days=day)))
            self.assertFalse(self._blocked(), f"blocked on day {day}")

    def test_it_expires_and_says_so(self):
        self.write_activation(str(date.today() - timedelta(days=seal.FRESH_INSTALL_DAYS)))
        self.assertTrue(self._blocked(), "the starter window never ended")
        self.assertEqual(
            self._reason(), "expired",
            "a shop out of TIME was told to connect the internet",
        )

    def test_the_window_is_the_servers_trial_length(self):
        """A build that granted more than the server does hands out free time."""
        from core.trial_activation import FALLBACK_TRIAL_DAYS

        self.assertEqual(seal.FRESH_INSTALL_DAYS, FALLBACK_TRIAL_DAYS)

    def test_the_dates_are_the_activation_and_activation_plus_the_window(self):
        window = seal.fresh_install_window()
        self.assertEqual(window["activation_date"], str(date.today()))
        self.assertEqual(
            window["expiry_date"],
            str(date.today() + timedelta(days=seal.FRESH_INSTALL_DAYS)),
        )

    def test_expiring_does_not_wipe_the_activation_record(self):
        """The window is measured FROM that record.

        An Offline expiry deletes activation.dat on purpose, to force the
        vendor's re-activation. Doing it here would hand the PC a brand-new
        window on the next launch -- the expiry would reset itself for ever.
        """
        # Past the window, and still after SEAL_CUTOVER -- an older date would
        # land in the legacy grace instead and prove nothing.
        self.write_activation(
            str(date.today() - timedelta(days=seal.FRESH_INSTALL_DAYS + 1))
        )
        lm._REAUTH_FORCED = False
        self.assertTrue(self._blocked())
        self.assertTrue(os.path.exists(lm._activation_path()))

    def test_a_signed_licence_always_wins(self):
        """The window never extends a real expiry, not by one day."""
        lapsed = str(date.today() - timedelta(days=1))
        seal.store_seal(self.make_blob(exp=lapsed), fresh=True)
        self.assertTrue(self._blocked(), "a lapsed signed licence was overridden")
        self.assertEqual(self._reason(), "expired")

    def test_a_pc_that_has_ever_held_a_licence_never_gets_one(self):
        seal.store_seal(self.make_blob(), fresh=True)
        os.remove(seal.seal_path())
        self.assertIsNone(seal.fresh_install_window())
        self.assertTrue(self._blocked())
        self.assertEqual(self._reason(), "needs_internet")

    def test_a_wiped_appdata_gets_nothing_until_it_activates_again(self):
        for name in os.listdir(self.appdata):
            os.remove(os.path.join(self.appdata, name))
        self.assertIsNone(seal.fresh_install_window())
        self.assertTrue(self._blocked())

    def test_a_tampered_blob_is_not_a_missing_one(self):
        seal.store_seal(self.make_blob(), fresh=True)
        with open(seal.seal_path(), "w", encoding="utf-8") as fh:
            fh.write("SATPUDA1.bm90YWJsb2I.bm90YXNpZw")
        self.assertTrue(self._blocked())
        self.assertEqual(self._reason(), "needs_internet")

    def test_a_pre_cutover_record_is_not_a_fresh_install(self):
        """Back-dating the record must not conjure a window out of the past."""
        self.write_activation("2026-05-01")
        self.assertIsNone(seal.fresh_install_window())

    def test_a_clock_set_forward_earns_nothing(self):
        self.write_activation(str(date.today() + timedelta(days=30)))
        self.assertIsNone(seal.fresh_install_window())

    def test_settings_shows_the_same_dates_that_are_enforced(self):
        """A blank expiry here is a Settings page claiming "no restriction"."""
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            state = lm.get_expiry_state(force_server=False)
        self.assertEqual(state["source"], "fresh")
        self.assertTrue(state["enabled"])
        self.assertEqual(
            state["expiry_date"],
            str(date.today() + timedelta(days=seal.FRESH_INSTALL_DAYS)),
        )
        self.assertTrue(state["access_allowed"])


class AnActivationThatWorkedCannotBecomeAFreshInstall(SealHarness):
    """The ordering trap, and the only way the starter window could be farmed.

    ``_write_activation`` REWRITES activation.dat, and the two marks a licence
    leaves -- the rollback ratchet and "this PC has held one" -- live inside it.
    The Online trial path used to store the blob and then write the record, so a
    PC that had completed a real sign-up and afterwards deleted license.seal
    looked exactly like a fresh install: activation dated today, no mark, no
    file. Three more days, every time, for one delete.
    """

    def test_the_online_trial_keeps_the_mark_it_just_earned(self):
        from core import trial_activation as ta

        blob = self.make_blob()
        response = {
            "token": "T",
            "store": {"store_id": "s1", "store_key": "Store_X",
                      "store_name": "X Medical", "android_key": "SC-XXXX"},
            "license": {"activation_date": str(date.today()),
                        "expiry_date": str(date.today() + timedelta(days=3))},
            "signed": blob,
        }
        with mock.patch("core.server_api.provision_trial", lambda **k: response), \
             mock.patch("core.server_api.save_session", lambda *a, **k: None), \
             mock.patch("core.server_api.ensure_store_session", lambda **k: {}), \
             mock.patch("core.store_link._save_local", lambda *a, **k: None), \
             mock.patch("core.store_manager.setup_initial_store_on_activation",
                        lambda name: None), \
             mock.patch("core.store_manager.get_active_store_key",
                        lambda: "Store_X"), \
             mock.patch("core.sync_prefs.set_sync_mode", lambda mode: None), \
             mock.patch("core.server_live._record_store_adoption",
                        lambda *a, **k: None), \
             mock.patch.object(ta, "already_set_up", lambda: False):
            out = ta.activate_trial("X Medical", sync_mode="online")

        self.assertTrue(out["ok"])
        self.assertEqual(seal.local_payload()[1], seal.STATE_SIGNED)

        # And now the delete, which is the whole attack.
        os.remove(seal.seal_path())
        self.assertTrue(
            seal.seal_ever_stored(),
            "the activation record was rewritten over the licence's own mark",
        )
        self.assertIsNone(seal.fresh_install_window())
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(lm.check_expiry())
            self.assertEqual(lm.expiry_block_reason(), "needs_internet")


class TheOfflineFallbackLeavesAWorkingTill(SealHarness):
    """The whole chain, from the refused sign-up to the next licence read.

    tests/test_activation_asks_two_things.py already asserted that a flat
    network still returns ok=True. Nothing asserted that the NEXT read let the
    app open, which is exactly the assertion that was missing when this shipped.
    """

    def test_an_offline_activation_with_no_server_still_opens_next_launch(self):
        from core import trial_activation as ta

        with mock.patch("core.store_manager.setup_initial_store_on_activation",
                        lambda name: None), \
             mock.patch("core.sync_prefs.set_sync_mode", lambda mode: None), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            result = ta._activate_offline_locally("Roshan Medical", "no internet")
            self.assertTrue(result["ok"])
            self.assertTrue(result["starter_licence"])
            self.assertEqual(
                result["expiry_date"],
                str(date.today() + timedelta(days=seal.FRESH_INSTALL_DAYS)),
            )
            with mock.patch.object(lm, "_is_online_mode", lambda: False):
                self.assertFalse(
                    lm.check_expiry(),
                    "the shop was blocked on the launch after it was set up",
                )

    def test_a_recovered_licence_still_wins_over_the_starter_window(self):
        """A machine the server remembers gets its own expiry, lapsed or not."""
        from core import trial_activation as ta

        lapsed = str(date.today() - timedelta(days=9))
        served = {"signed": self.make_blob(exp=lapsed, act="2026-08-01")}
        with mock.patch("core.store_manager.setup_initial_store_on_activation",
                        lambda name: None), \
             mock.patch("core.sync_prefs.set_sync_mode", lambda mode: None), \
             mock.patch("core.server_api.store_token_for_active", lambda *a, **k: ""), \
             mock.patch("core.server_api.recover_signed_license", lambda b, **k: served):
            seal._LAST_FETCH_AT = 0.0
            result = ta._activate_offline_locally("Roshan Medical", "refused")
        self.assertEqual(result["expiry_date"], lapsed)
        self.assertFalse(result["starter_licence"])
        with mock.patch.object(lm, "_is_online_mode", lambda: False), \
             mock.patch.object(seal, "fetch_seal", lambda **k: None):
            self.assertTrue(lm.check_expiry())


class TheBuildCarriesAKey(unittest.TestCase):
    def test_a_real_public_key_is_compiled_in(self):
        """A build with no key falls back to the old, defeatable rules.

        That fallback is deliberate -- an unkeyed build must not block every shop
        at once -- which is exactly why it needs a test standing over it. If this
        fails, the release would ship with the licence check quietly disabled.
        """
        self.assertTrue(
            seal.keys_configured(),
            "core/license_seal.py carries no licence public key; run "
            "`node make-license-key.mjs` on the server and paste the block it prints",
        )
        for kid, der_b64 in seal._PUBLIC_KEYS.items():
            with self.subTest(kid=kid):
                self.assertRegex(kid, r"^[0-9a-f]{16}$")
                n, e = seal._rsa_numbers(base64.b64decode(der_b64))
                self.assertGreaterEqual(n.bit_length(), 2048)
                self.assertEqual(e, 65537)



class TheStarterWindowCannotBeRenewed(SealHarness):
    """Three renewal routes an adversarial review proved, and now none of them pay.

    The starter window exists for a shop that activated with no server to answer
    it. It must not become a licence the shop can re-issue to itself: before
    2026-09-16 it could, three ways, none of which needed vendor code --
    re-activate with the two literals compiled into the build, delete
    activation.dat and sign up Offline again, or (for a paying shop that had
    expired) delete the licence file as well and be treated as new.
    """

    def setUp(self):
        super().setUp()
        self.write_activation(str(date.today() - timedelta(days=seal.FRESH_INSTALL_DAYS)))
        self.assertTrue(seal.fresh_install_window()["lapsed"], "the window did not lapse")

    def _reactivate(self):
        """What the vendor re-activation form does when all three factors pass."""
        lm._write_activation(lm._get_hardware_hash())

    def test_re_activating_does_not_hand_out_another_window(self):
        self._reactivate()
        window = seal.fresh_install_window()
        self.assertTrue(window["lapsed"], "re-activation bought three more days")
        self.assertEqual(
            window["activation_date"],
            str(date.today() - timedelta(days=seal.FRESH_INSTALL_DAYS)),
            "the window moved to today's date",
        )

    def test_deleting_the_activation_record_does_not_forget_the_window(self):
        os.remove(lm._activation_path())
        self.write_activation(str(date.today()))           # sign up again, Offline
        window = seal.fresh_install_window()
        self.assertTrue(window["lapsed"], "deleting the record renewed the window")

    def test_a_shop_that_has_sold_something_gets_no_window_at_all(self):
        import sqlite3

        from core import store_manager

        key = "Store_Test"
        store_manager.save_registry({"stores": [{"store_key": key, "display_name": "Test"}],
                                     "active_store": key})
        db = store_manager.get_store_db_path(key)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, bill_no TEXT)")
        con.execute("INSERT INTO sales (id, bill_no) VALUES (1, 'SCB1')")
        con.commit()
        con.close()
        seal._TRADING_CACHE.clear()
        self.write_activation(str(date.today()))
        self.assertIsNone(
            seal.fresh_install_window(),
            "a shop with bills on it was handed a fresh-install window",
        )

    def test_an_empty_shop_folder_is_still_a_fresh_install(self):
        import sqlite3

        from core import store_manager

        key = "Store_Empty"
        store_manager.save_registry({"stores": [{"store_key": key, "display_name": "Empty"}],
                                     "active_store": key})
        con = sqlite3.connect(store_manager.get_store_db_path(key))
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, bill_no TEXT)")
        con.commit()
        con.close()
        seal._TRADING_CACHE.clear()
        self.write_activation(str(date.today()))
        window = seal.fresh_install_window()
        self.assertIsNotNone(window, "a shop that has sold nothing is a fresh install")
        self.assertEqual(
            window["activation_date"],
            str(date.today() - timedelta(days=seal.FRESH_INSTALL_DAYS)),
            "an empty shop still qualifies, and still on its FIRST window",
        )


class AnActivationRecordKeepsWhatItMustNotForget(SealHarness):
    """_write_activation is the file every re-activation rewrites."""

    def test_the_first_activation_date_is_carried_forward(self):
        self.write_activation("2026-09-10")
        lm._write_activation(lm._get_hardware_hash())
        record = lm._read_activation()
        self.assertEqual(record["first_date"], "2026-09-10")
        self.assertEqual(record["date"], str(date.today()), "today's activation is still recorded")

    def test_a_licence_this_pc_has_held_is_not_forgotten(self):
        self.write_activation(str(date.today()))
        seal._mark_seal_seen()
        self.assertTrue(seal.seal_ever_stored())
        lm._write_activation(lm._get_hardware_hash())
        self.assertTrue(
            seal.seal_ever_stored(),
            "re-activating made a PC that had held a licence look new again",
        )


if __name__ == "__main__":
    unittest.main()
