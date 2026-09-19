# factledger — typed facts with structural truth

Μνήμη για agents/ανθρώπους όπου **η αλήθεια επιβάλλεται από τη δομή, όχι από κρίση μοντέλου**.

Για κάθε `(scope, subject, predicate)` υπάρχει **ακριβώς μία ενεργή γραμμή**. Νέα τιμή
**κλείνει** την προηγούμενη (`valid_to` + `superseded_by`) — δεν τη σβήνει. Έτσι δεν
μπορούν να συνυπάρξουν αντιφατικές εκδοχές ως ισότιμες.

## Γιατί υπάρχει

Δοκιμάστηκε το mem0 (self-hosted, 65k★) στο σενάριο «ποια είναι η **τρέχουσα** τιμή;»
μετά από διόρθωση. Αποτέλεσμα: η **ξεπερασμένη** τιμή επέστρεψε **πρώτη** (score 0,79)
και η σωστή τρίτη (0,77) — ο UPDATE στο αυτόματο extraction ενεργοποιούνταν μόνο ως ADD.
Η αποτυχία δεν ήταν του embedding: ήταν ότι η απόφαση «αντικαθίσταται;» δινόταν σε LLM.

Εδώ η αντικατάσταση είναι **λειτουργία του κλειδιού**, όχι απόφαση: το λάθος δεν μπορεί
να συμβεί δομικά.

## Χρήση

```bash
python factledger.py add --scope silktales --subject product.70x70 --predicate cost \
    --value 22 --source "τιμοκατάλογος 2026-09" --confidence stated

python factledger.py show --subject product.70x70          # τρέχουσα αλήθεια
python factledger.py show --subject product.70x70 --as-of 2026-03-01   # τι ίσχυε τότε
python factledger.py history --subject product.70x70       # τι άλλαξε πότε και με ποια πηγή
python factledger.py search kraken
python factledger.py compile --budget 600 --out MEMORY.md  # συμπαγής όψη για prompt
python factledger.py check                                  # έλεγχος ακεραιότητας
python factledger.py stats
```

`--source` είναι **υποχρεωτικό**: γεγονός χωρίς πηγή δεν γράφεται.

## Σχήμα

| στήλη | σημασία |
|---|---|
| `id` | `f00001`… σταθερό |
| `scope` | `user` / `profile` / `project` / `session` — το «ανά ποιον» της ανάκτησης |
| `subject` | το αντικείμενο (`product.70x70`, `kraken`) |
| `predicate` | η ιδιότητα (`cost`, `access`) — περνά από alias map |
| `value` | η τιμή |
| `valid_from` / `valid_to` | διάστημα ισχύος· κενό `valid_to` = ανοιχτή |
| `superseded_by` | ποια γραμμή την αντικατέστησε |
| `source` | από πού προέκυψε (υποχρεωτικό) |
| `confidence` | `stated` > `derived` > `assumed` (σειρά στο compile) |

Δεδομένα: **ένα TSV**, ανθρωπίνως αναγνώσιμο, grep-άσιμο, με καθαρό `git diff`.
Προαιρετικό `aliases.tsv` (`canonical<TAB>alias1,alias2`) για συνώνυμα predicates.

## Σχεδιαστικές επιλογές

- **Χρονικά διαστήματα, όχι «τελευταία εγγραφή»** — η `show --as-of` απαντά τι ίσχυε τότε
  (απαραίτητο για ελέγχους/αναδρομικές ερωτήσεις).
- **Προγραμματισμένη αλλαγή** δεν κλείνει την τρέχουσα αλήθεια πριν έρθει η ώρα της.
- **`compile` με όριο χαρακτήρων** — μπαίνει σε prompt χωρίς να το φουσκώνει· το υπόλοιπο
  μένει προσβάσιμο με `show`/`search`.
- **Μηδέν εξαρτήσεις** (stdlib), μηδέν δίκτυο, μηδέν LLM σε read/write· LLM χρειάζεται
  μόνο αν κάποιος θέλει αυτόματη εξαγωγή γεγονότων από transcripts (εκτός πυρήνα).
- **Το λάθος μένει ορατό**: τίποτα δεν διαγράφεται, μόνο κλείνει — audit trail δωρεάν.

## Τι ΔΕΝ είναι

- Δεν κάνει σημασιολογική/vector αναζήτηση σε ελεύθερο κείμενο — γι' αυτό υπάρχει
  ξεχωριστό σύστημα (π.χ. GraphRAG). **Ledger = αλήθεια, vector store = ομοιότητα.**
- Δεν εξάγει μόνο του γεγονότα από συνομιλίες (παρέχεται μόνο το σχήμα/CLI για να γίνει).
- Δεν λύνει το dedup οντοτήτων έξω από κλειδιά: τα συνώνυμα τα δηλώνεις στο `aliases.tsv`.

## Tests

```bash
python -m unittest discover -s tests -v
```

Το κρίσιμο: `test_supersede_closes_previous` — η νέα τιμή κλείνει την παλιά, μία ενεργή
αλήθεια, πλήρες ιστορικό.

## Άδεια

MIT — βλ. `LICENSE`.
