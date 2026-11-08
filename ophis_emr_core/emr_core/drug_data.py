"""The national drug master, loaded from Health Canada's Drug Product Database (DPD).

The DPD publishes an extract of every marketed drug product as a zip of comma-separated
files without header rows. `import_dpd` replaces the `Drug Product` table with the human
products in it. Prescriptions copy what they need from a product rather than link to it, so
the table can be replaced at any time:

    bench --site <site> execute ophis_emr_core.emr_core.drug_data.import_dpd
"""

import csv
import io
import zipfile

import frappe
from frappe.utils import now

DPD_EXTRACT_URL = "https://www.canada.ca/content/dam/hc-sc/documents/services/drug-product-database/allfiles.zip"

# Column positions in the extract's files, from the DPD's published file layout.
DRUG = {"code": 0, "class": 2, "din": 3, "brand_name": 4}
INGREDIENT = {"code": 0, "name": 2, "strength": 4, "strength_unit": 5, "dosage_value": 7, "dosage_unit": 9}
SECOND_COLUMN_FILES = {"form.txt": 2, "route.txt": 2, "schedule.txt": 1, "comp.txt": 3}

# Schedules under the Controlled Drugs and Substances Act: the drugs a pharmacy must see
# extra identification for.
MONITORED_SCHEDULES = ("NARCOTIC", "CONTROLLED DRUGS", "TARGETED SUBSTANCES")
FIELDS = ["din", "brand_name", "generic_name", "strength", "form", "route", "schedule", "monitored", "company"]


def import_dpd(path: str | None = None) -> int:
	"""Replace the drug master with the DPD extract at `path`, or the current one from Health Canada."""
	if path:
		with open(path, "rb") as file:
			content = file.read()
	else:
		import requests

		response = requests.get(DPD_EXTRACT_URL, timeout=120)
		response.raise_for_status()
		content = response.content

	with zipfile.ZipFile(io.BytesIO(content)) as archive:
		files = {
			name: list(csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8", errors="replace")))
			for name in ("drug.txt", "ingred.txt", *SECOND_COLUMN_FILES)
		}
	products = build_products(files)
	replace_products(products)
	frappe.db.commit()  # nosemgrep: a data load run from the command line
	return len(products)


def build_products(files: dict[str, list[list[str]]]) -> list[dict]:
	"""One product per human DIN, with its ingredients, form, route and schedule gathered up."""

	def gather(name):
		values = {}
		for row in files.get(name, []):
			value = row[SECOND_COLUMN_FILES[name]].strip()
			if value and value not in values.setdefault(row[0], []):
				values[row[0]].append(value)
		return values

	forms, routes, schedules, companies = (gather(name) for name in SECOND_COLUMN_FILES)

	ingredients = {}
	for row in files["ingred.txt"]:
		strength = " ".join(filter(None, [row[INGREDIENT["strength"]].strip(), row[INGREDIENT["strength_unit"]].strip()]))
		per = " ".join(filter(None, [row[INGREDIENT["dosage_value"]].strip(), row[INGREDIENT["dosage_unit"]].strip()]))
		ingredients.setdefault(row[INGREDIENT["code"]], []).append(
			# "NORTRIPTYLINE (NORTRIPTYLINE HYDROCHLORIDE)" names the substance, then its salt.
			(row[INGREDIENT["name"]].split(" (")[0].strip(), f"{strength} / {per}" if per else strength)
		)

	products = {}
	for row in files["drug.txt"]:
		code, din = row[DRUG["code"]], row[DRUG["din"]].strip()
		if row[DRUG["class"]] != "Human" or not din:
			continue
		schedule = ", ".join(schedules.get(code, []))
		products[din] = {
			"din": din,
			"brand_name": row[DRUG["brand_name"]].strip()[:140],
			"generic_name": " / ".join(name for name, _strength in ingredients.get(code, []))[:140],
			"strength": " / ".join(strength for _name, strength in ingredients.get(code, []) if strength)[:140],
			"form": ", ".join(forms.get(code, []))[:140],
			"route": ", ".join(routes.get(code, []))[:140],
			"schedule": schedule[:140],
			"monitored": int(any(marker in schedule for marker in MONITORED_SCHEDULES)),
			"company": (companies.get(code) or [""])[0][:140],
		}
	return list(products.values())


def replace_products(products: list[dict]):
	frappe.db.delete("Drug Product")
	timestamp = now()
	frappe.db.bulk_insert(
		"Drug Product",
		["name", "creation", "modified", "owner", "modified_by", "docstatus", *FIELDS],
		[
			(product["din"], timestamp, timestamp, "Administrator", "Administrator", 0, *(product[field] for field in FIELDS))
			for product in products
		],
	)
