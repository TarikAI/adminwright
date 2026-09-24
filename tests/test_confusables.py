# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Look-alike and invisible characters cannot hide a placeholder, and honest text in any
script is not one.

The table is generated from Unicode's confusables data (UTS #39) by
scripts/maintainer/gen_confusables.py. Before it, nine disguises passed the scanner:
Armenian 'oh', Greek kappa, small capitals, the Hangul fillers, the Braille blank,
all-Cyrillic 'mock', Cherokee letters and the musical null notehead. The old table also
folded Russian 'Moskva' (Moscow) into 'Mockba' and called an honest manifest a mock.
"""

import unittest

from cli_helpers import acm


def u(text):
    """'m{585}ck' -> 'm' + U+0585 + 'ck', so this file stays ASCII."""
    import re
    return re.sub(r"\{([0-9A-Fa-f]{2,6})\}", lambda m: chr(int(m.group(1), 16)), text)


DISGUISED = [
    ("Armenian oh", u("m{585}ck")),
    ("Greek kappa", u("moc{3BA}")),
    ("small capitals", u("{1D0D}{1D0F}{1D04}{1D0B}")),
    ("Hangul filler", u("mo{3164}ck")),
    ("halfwidth Hangul filler", u("mo{FFA0}ck")),
    ("Braille blank", u("m{2800}ock")),
    ("fullwidth", u("{FF4D}{FF4F}{FF43}{FF4B}")),
    ("mathematical bold", u("{1D426}{1D428}{1D41C}{1D424}")),
    ("Cyrillic o", u("m{43E}ck")),
    ("zero-width space", u("mo{200B}ck")),
    ("all-Cyrillic", u("{43C}{43E}{441}{43A}")),
    ("soft hyphen", u("mo{AD}ck")),
    ("combining acute", u("mo{301}ck")),
    ("tag character", u("mo{E006D}ck")),
    ("Cherokee", u("Mo{13DF}{13E6}")),
    ("Greek capital iota in FIXME", u("F{399}XME")),
    ("Cyrillic dze in stub", u("{455}tub")),
    ("musical null notehead", u("to{1D159}do")),
]

HONEST = [
    ("German", u("Bestellungen f{FC}r H{E4}ndler")),
    ("Arabic", u("{637}{644}{628}{627}{62A} {627}{644}{639}{645}{644}{627}{621}")),
    ("Chinese", u("{8BA2}{5355}{8868}")),
    ("Russian, Moscow", u("{41C}{43E}{441}{43A}{432}{430}, {43C}{43E}{441}{43A}{43E}{432}{441}{43A}{438}{439} "
                         "{437}{430}{43A}{430}{437}")),
    ("Greek", u("{3A0}{3B1}{3C1}{3B1}{3B3}{3B3}{3B5}{3BB}{3AF}{3B5}{3C2} {3C0}{3B5}{3BB}{3B1}{3C4}{3CE}{3BD}")),
    ("Armenian", u("{54A}{561}{57F}{57E}{565}{580}{576}{565}{580}")),
    ("Japanese", u("{6CE8}{6587}{30C6}{30FC}{30D6}{30EB}{3068}T{30B7}{30E3}{30C4}")),
    ("Korean", u("{C8FC}{BB38} {D14C}{C774}{BE14}")),
    ("Hebrew", u("{5D4}{5D6}{5DE}{5E0}{5D5}{5EA} {5DC}{5E7}{5D5}{5D7}{5D5}{5EA}")),
    ("Hindi", u("{917}{94D}{930}{93E}{939}{915} {906}{926}{947}{936}")),
    ("Turkish", u("Sipari{15F} tablosu {131}s{131}k")),
    ("Danish", u("Ordrer for forhandlere p{E5} {D8}sterbro, m{F8}bler og sm{F8}r")),
    ("Vietnamese", u("{110}{1A1}n {111}{1EB7}t h{E0}ng")),
    ("Polish", u("Zam{F3}wienia klient{F3}w w {141}odzi")),
    ("units", u("5 {B5}s, 10 k{3A9}, 1920{D7}1080, {A9} 2026")),
]


class TestConfusables(unittest.TestCase):
    def test_every_disguise_reads_as_the_placeholder_it_is(self):
        missed = [label for label, text in DISGUISED if not acm.placeholder_match(text)]
        self.assertEqual(missed, [])

    def test_honest_text_in_other_scripts_is_not_a_placeholder(self):
        flagged = [(label, acm.placeholder_match(text)) for label, text in HONEST
                   if acm.placeholder_match(text)]
        self.assertEqual(flagged, [])

    def test_a_capital_look_alike_keeps_the_camel_case_hump(self):
        # 'mock' + Cyrillic DZE + 'ervice': the capital must fold to a capital, or the
        # identifier stays one word and the placeholder hides inside it.
        self.assertEqual(acm.fold_confusables(u("mock{405}ervice")), "mockService")
        self.assertEqual(acm.placeholder_match(u("{43C}{43E}ckService")), "mock")

    def test_plain_ascii_is_returned_unchanged(self):
        text = "UserRepository.findForAdmin via postgres:users"
        self.assertIs(acm.fold_confusables(text), text)


class TestProseTodo(unittest.TestCase):
    """Spanish and Portuguese 'todo' means 'all'; in prose only a to-do marker counts."""

    def test_the_word_all_is_not_a_placeholder_in_prose(self):
        for text in ("Ver todo el historial", "Listar todos los pedidos", "Todo o histórico"):
            self.assertIsNone(acm.placeholder_match(text, prose=True), text)

    def test_a_to_do_marker_still_is(self):
        for text in ("TODO wire the gateway", "todo: wire it", "// todo fix", "@todo", "todo(ana) later"):
            self.assertTrue(acm.placeholder_match(text, prose=True), text)

    def test_implementation_fields_stay_strict(self):
        self.assertTrue(acm.placeholder_match("todosHandler", prose=False))
        self.assertTrue(acm.placeholder_match("Listar todos", prose=False))

    def test_an_evidence_log_in_spanish_passes(self):
        log = u("✓ deber{ED}a listar todos los usuarios (12 ms)\n  6 passed\n")
        self.assertIsNone(acm.scan_file_placeholder(log))
        self.assertTrue(acm.scan_file_placeholder("TODO: replace with the real run\n"))


if __name__ == "__main__":
    unittest.main()
