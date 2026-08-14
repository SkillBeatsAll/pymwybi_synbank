# Standard Bank & SU Hackathon 2026

- Team: **Put your money where your byte is**
- Members: Vihan Allan, Joel Cedras, Viajul Moodley, Rahul Maharaj

## Data preparation

The raw CSV files in `data/` are immutable inputs. First unzip the "raw.tgz" file. The reproducible cleaning
pipeline writes analysis-ready Parquet files and an audit report to
`data/processed/`:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m src.syn_wallet.clean_data --input-dir data/raw --output-dir data/processed
>>>>>>> Stashed changes
```

The pipeline removes only exact duplicate canonical records, standardises the
transactional `currency` code to uppercase, and retains records with conflicting
identifiers. Retained conflicts are marked with `has_identifier_conflict`.

Parquet is used because the raw transactional CSV alone is about 375 MB and is
repeatedly scanned during analysis. The output is compressed, typed, and much
faster to query while retaining the source business fields.


To run the notebook:
```bash
.venv/bin/jupyter notebook wallet_twin.ipynb
```

