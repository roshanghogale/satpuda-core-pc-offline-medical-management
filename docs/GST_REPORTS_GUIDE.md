# GST Reports — vaparnyachi paddhat

Satpuda Core madhle GST reports kase kadhayche, kay aahe ani kase tapasayche, ani itar sagle reports kuthe aahet. (App madhe: GST Reports → Madat (?))

## 1. Suruvatila ekda (setup)

- Settings → Pharmacy → GSTIN: GST nondani asel tar dukanacha GSTIN bhara (GSTR-1 JSON ani rajya / Place of Supply sathi). Nondani nasel tar rikama theva — sagle reports tari chaltat, kuthlihi bandhi nahi.
- B2B grahak (hospital, clinic, doctor, dusre dukan je GSTIN deun bill magtat): Settings → Contacts → Customers → Edit → "GSTIN (B2B bill sathi)" ani "Kadhi pasun" bhara → "GSTIN save kara". Tya tarkhepasunchya bills var tyancha GSTIN chhapla jaato ani te bills B2B madhe jaatat.
- Saadhe (retail) grahak: kahich bharayche nahi — te bills aapoaap B2CS madhe jaatat.
- Aushadh master madhe HSN code (4 / 6 / 8 ank) ani GST % bhara. Nasel tar "Checks" madhe disel.

## 2. Report ughadne

- Home var "GST Reports" button, kiva Home var astana G dabaa.
- Mahina (GSTR-1 / 3B monthly), Timahi (QRMP: Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar) kiva Tarikh pasun–paryant nivda. Aakde laagech mojle jaatat.
- Online store: server varche bills; Offline store: ya PC varche bills — donhi madhe sarkhech report.

## 3. Tabs madhe kay aahe

- GSTR-3B + Tally: 3.1(a) karpaatra vikri ani tax, 3.1(c) 0% vikri, 3.2 dusrya rajyat vikri, 4 ITC (supplier bills madhun) − parat kelela maal, 5 0% kharedi. Khali "Tally": bills cha ekun = taxable + tax + round-off; PHARAK 0 asla pahije.
- Sales register: pratyek bill, GST rate-wise taxable / CGST / SGST — chhaplelya bill pramane paisa-paisa.
- B2B: GSTIN asnarya grahakanchi bills (invoice-wise). B2CS: baki saglya bills cha rate-wise ekun (returns vajaa karun). B2CL: dusrya rajyatil, ₹1 lakh peksha jaast nondani nasleli bills (retail madhe kvachit).
- Credit notes: B2B bills varche returns (CDNR). Saadhya grahakanche returns B2CS madhunach vajaa hotat — he GST niyam aahe.
- HSN: HSN-wise ekun, B2B ani B2C vegle (GSTR-1 Table 12). Documents: bill no. pasun–paryant; madhle kadhlele numbers "Cancelled".
- Purchase ITC: supplier-wise bill, tyacha GSTIN, ITC. Supplier la GSTIN nasel tar ITC milat nahi (te vegle dakhavle aahe).
- Checks: je bills tapasayla have — GST % bill var navhta, HSN nahi/chukla, aushadh lines nasleli bills, mool bill nasleli returns. Andaz lavla jaat nahi; he durust kara kiva CA la dakhava.

## 4. Filed / Tally (history ani julavni)

- Return file kelyavar (kiva CA la dilyavar) "Filed / Tally" tab → note lihun "… Filed mhanun jatan kara".
- Nantar kadhihi "Aaj shi julva": file kelyanantar konta bill badalla, kadhla, ushira nondla, kiva return badalla te bill-wise disel — pudhchya return madhe durusti kara.
- He jatan kelele aakde store sobat rahtat (Online madhe saglya PC var disatat; Online ⇄ Offline badalla tari sobat jaatat).

## 5. File ani print

- Excel (sagle tables): GST portal chya GSTR-1 Excel sarkhi sheets (b2b, b2cs, cdnr, hsn(b2b), hsn(b2c), docs …) + 3B, Tally, registers, Checks — CA la hich dya.
- GSTR-1 JSON: portal sathi. Upload karnyaadhi GST offline tool madhe ekda ughadun tapasa.
- CSV / PDF (ha tab), Print (ha tab): Printer kiva Dot matrix, A4 aadva / ubha.

## 6. CA sathi mahatvache

- ITC (3B table 4) purchase entry madhun aahe; portal varchya GSTR-2B shi julvun ghya — supplier ne return bharla tarach ITC milto.
- Composition scheme madhe asnarya dukanala he reports lagu nahit (tithe GSTR-4 / CMP-08).
- HSN: ₹5 koti paryant turnover la 4 ank, tyapeksha jaast la 6 ank aavashyak.
- Sagle aakde bill var chhaplelya GST pramane (bill discount pratyek line var vaatun, paisa half-up); round-off taxable madhe dharla jaat nahi.

## 7. Shortcuts

- Home: G = GST Reports · B = New Bill · P = Purchase · I = Inventory · E = Export.
- GST Reports window: Escape = band.
- Saglya shortcuts chi yaadi: Settings → Shortcuts → Keyboard Shortcuts.

## 8. Sagle reports kuthe aahet

- Home → GST Reports (G): GSTR-3B + Tally, Sales GST register, GSTR-1 (B2B, B2CS / B2CL, Credit notes, HSN B2B / B2C, Documents), Purchase ITC register + rate-wise, Checks, Filed / Tally.
- Sales History → Export (Ctrl+E): Current view, Sales Register, Monthly Summary, Daily Sales Summary, Customer Due, Doctor-wise Sales, Payment Mode, Schedule Report (Classic / Sign, ubha / aadva).
- Purchase History → Export (Ctrl+E): Current view, Purchase Register, Monthly Summary, Supplier Due, GST Purchase Report.
- Inventory → Export (Ctrl+E): Current view, Stock Statement, Near Expiry, Expired Stock, Schedule-wise Stock.
- Home → Export (E): Sales / Purchases / Inventory / All — purna data.
- Settings → Alert & Monitoring: Low Stock, Out of Stock, Expired, Near Expiry, Customer Dues — mahina / varsh filter, Export / Print (ek kiva saglya yaadya ekatra).
- Settings → Ledger: Supplier Ledger, Customer Ledger. Settings → Contacts → Customers: Customer list, Customer due list.
- Settings → Data & System → Export Data. Returns → history madhun return print.
- Pratyek export: CSV / Excel / PDF, kiva print — Printer kiva Dot matrix, A4 ubha / aadva.
