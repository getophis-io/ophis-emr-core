"""Field-level encryption for patient identifiers.

The columns in `ENCRYPTED_FIELDS` hold ciphertext in the database. The controllers encrypt them
as they write and `Patient` decrypts them, its emergency contacts included, as it loads, so a
document in memory always holds plain values and code working with `frappe.get_doc("Patient",
...)` needs nothing special. Anything that reads the columns directly (`frappe.get_all`,
`frappe.db.get_value`, SQL) gets ciphertext and must call `decrypt` or `decrypt_rows`; anything
that writes them directly (`frappe.db.set_value`, `db_set`) bypasses encryption and must not be
used for these fields.

Ciphertext cannot be searched, so lookups go through a blind index: a keyed hash of the
normalized value, stored beside it (`INDEX_FIELDS`), which matches exact values only.

The key is the `PII_ENCRYPTION_KEY` environment variable of the processes running the bench,
never the database or the site config, so a database dump or backup on its own does not reveal
these fields. Lose the key and the fields are lost with it.

Keys are rotated without downtime. Every value records the key that encrypted it, and
`PII_ENCRYPTION_KEYS_PREVIOUS` (comma-separated) lists retired keys that are still read:

1. Make a key with `generate_key`. Set it as `PII_ENCRYPTION_KEY` and move the old one to
   `PII_ENCRYPTION_KEYS_PREVIOUS`. Restart. New writes use the new key; old values still read,
   and lookups match blind indexes made under either key.
2. `bench --site <site> execute ophis_emr_core.emr_core.phi.rotate` re-encrypts and re-indexes
   everything still under an old key. It can run while the clinic works and be run again.
3. When `ophis_emr_core.emr_core.phi.count_on_old_keys` returns 0, drop the old key from
   `PII_ENCRYPTION_KEYS_PREVIOUS` and restart.
"""

import base64
import contextlib
import functools
import hashlib
import hmac
import os
import re
import secrets

import frappe
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from frappe import _
from frappe.utils import getdate

KEY_VARIABLE = "PII_ENCRYPTION_KEY"
PREVIOUS_KEYS_VARIABLE = "PII_ENCRYPTION_KEYS_PREVIOUS"

# The context every key is derived under. It is part of every key, so changing it makes every
# stored value unreadable and every blind index stale. It keeps the name of the app this module
# was written in; leave it as it is.
KDF_CONTEXT = b"strmlne_emr "

# Marks a stored value as ciphertext: `phi1:<key id>:<Fernet token>`.
PREFIX = "phi1:"

ENCRYPTED_FIELDS = {
	"Patient": (
		"health_card_number",
		"health_card_version",
		"health_card_expiry",
		"mobile_phone",
		"home_phone",
		"email",
	),
	"Patient Emergency Contact": ("phone", "email", "address"),
}

# Other clinic apps add their own fields through the `phi_encrypted_fields` hook, a dict of
# DocType to field names, read by `encrypted_fields`; this one only holds the core's.

# Blind index column for each encrypted field that is looked up by value.
INDEX_FIELDS = {
	"Patient": {
		"health_card_number": "health_card_number_index",
		"mobile_phone": "mobile_phone_index",
		"home_phone": "home_phone_index",
		"email": "email_index",
	},
}

# Longest plain value that still fits a 255-character column once encrypted.
MAX_PLAIN_LENGTH = 120

# Shown in Frappe's Version log in place of an encrypted field's old and new values.
REDACTED = "(encrypted)"


class MissingKeyError(frappe.ValidationError):
	pass


class Key:
	"""One secret, and the encryption and blind index keys derived from it."""

	def __init__(self, secret: str):
		try:
			master = base64.urlsafe_b64decode(secret.strip())
		except ValueError:
			master = b""
		if len(master) != 32:
			frappe.throw(_("An encryption key must be 32 random bytes, base64-encoded. Make one with generate_key."), MissingKeyError)

		def derive(purpose: bytes) -> bytes:
			return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=KDF_CONTEXT + purpose).derive(master)

		# Says which key wrote a value without saying anything about the key.
		self.id = hashlib.sha256(derive(b"key id")).hexdigest()[:8]
		self.fernet = Fernet(base64.urlsafe_b64encode(derive(b"phi encryption")))
		self.index_key = derive(b"phi blind index")


def encrypted_fields() -> dict[str, tuple[str, ...]]:
	"""Every encrypted field, by DocType: `ENCRYPTED_FIELDS` and what other apps' hooks declare.

	An app that keeps patient information of its own (a patient portal's messages, for instance) lists its
	fields under `phi_encrypted_fields` in its hooks and gives its controllers
	`EncryptedFieldsMixin`. Its fields are then rotated, redacted and counted with the core's.
	"""
	fields = dict(ENCRYPTED_FIELDS)
	for doctype, names in (frappe.get_hooks("phi_encrypted_fields") or {}).items():
		fields[doctype] = tuple(dict.fromkeys((*fields.get(doctype, ()), *names)))
	return fields


def generate_key() -> str:
	return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def ensure_key():
	"""Stop an install or migrate on a bench started without the key. Called by both."""
	get_keys()


@functools.lru_cache(maxsize=4)
def load_keys(current: str, previous: str) -> tuple[Key, ...]:
	return tuple(Key(secret) for secret in [current, *previous.split(",")] if secret.strip())


def get_keys() -> tuple[Key, ...]:
	"""Every key that can be read, the current one, which writes, first."""
	current = os.environ.get(KEY_VARIABLE)
	if not current:
		frappe.throw(
			_("{0} is not set, so patient identifiers cannot be read or saved. Set it in the environment of the bench.").format(
				KEY_VARIABLE
			),
			MissingKeyError,
		)
	return load_keys(current, os.environ.get(PREVIOUS_KEYS_VARIABLE, ""))


def is_encrypted(value) -> bool:
	return isinstance(value, str) and value.startswith(PREFIX)


def key_id(value: str) -> str | None:
	return value[len(PREFIX) :].split(":", 1)[0] if is_encrypted(value) else None


def is_current(value) -> bool:
	"""Whether a value needs no work: empty, or encrypted under the current key."""
	return not value or key_id(value) == get_keys()[0].id


def encrypt(value) -> str | None:
	if not value or is_encrypted(value):
		return value
	key = get_keys()[0]
	return f"{PREFIX}{key.id}:{key.fernet.encrypt(str(value).encode()).decode()}"


def decrypt(value: str | None) -> str | None:
	"""The plain value. Values without the prefix are returned as they are: they were stored
	before encryption was switched on and are encrypted by the next save or the migration patch."""
	if not is_encrypted(value):
		return value
	kid, sep, token = value[len(PREFIX) :].partition(":")
	if not sep:
		# Written before values named their key.
		kid, token = None, value[len(PREFIX) :]
	# The named key first; the others too, should a key id ever collide.
	for key in sorted(get_keys(), key=lambda key: key.id != kid):
		try:
			return key.fernet.decrypt(token.encode()).decode()
		except InvalidToken:
			continue
	frappe.throw(
		_("A patient identifier was encrypted with a key this bench does not have (key id {0}).").format(kid),
		MissingKeyError,
	)


def reencrypt(value):
	return value if is_current(value) else encrypt(decrypt(value))


def decrypt_rows(doctype: str, rows: list[dict]) -> list[dict]:
	"""Decrypt, in place, the encrypted fields of rows read with `frappe.get_all` and the like."""
	fields = encrypted_fields().get(doctype, ())
	for row in rows:
		for field in fields:
			if field in row:
				row[field] = decrypt(row[field])
	return rows


def normalize_for_index(field: str, value: str | None) -> str | None:
	if not value:
		return None
	if field.endswith("_phone"):
		# The last ten digits, so +1 416-555-0101 and (416) 555 0101 index the same.
		digits = re.sub(r"\D", "", value)[-10:]
		return digits or None
	return re.sub(r"[\s-]", "", value).upper() or None


def blind_index(field: str, value: str | None, key: Key | None = None) -> str | None:
	"""A keyed hash of the normalized value: equal values give equal hashes, nothing else is revealed."""
	normalized = normalize_for_index(field, value)
	if not normalized:
		return None
	key = key or get_keys()[0]
	# Both phone fields share one namespace, so a number matches whichever field holds it.
	kind = "phone" if field.endswith("_phone") else field
	return hmac.new(key.index_key, f"{kind}:{normalized}".encode(), hashlib.sha256).hexdigest()


def blind_indexes(field: str, value: str | None) -> list[str]:
	"""The value's blind index under every readable key, for lookups that must also find rows
	not yet re-indexed after a key rotation."""
	return [index for key in get_keys() if (index := blind_index(field, value, key))]


def set_indexes(doc):
	for field, index_field in INDEX_FIELDS.get(doc.doctype, {}).items():
		doc.set(index_field, blind_index(field, doc.get(field)))


def validate_lengths(doc):
	"""Text columns hold any length; a 255-character column only so much once encrypted."""
	for field in encrypted_fields().get(doc.doctype, ()):
		value = doc.get(field)
		if doc.meta.get_field(field).fieldtype in ("Small Text", "Text", "Long Text"):
			continue
		if value and len(str(value)) > MAX_PLAIN_LENGTH:
			frappe.throw(
				_("{0} cannot be longer than {1} characters.").format(_(doc.meta.get_label(field)), MAX_PLAIN_LENGTH)
			)


def normalize_date(value) -> str | None:
	"""A date kept in an encrypted text column, as YYYY-MM-DD. Throws if it is not a date."""
	return getdate(value).isoformat() if value else None


def get_encrypted_tables(doctype: str) -> list[tuple[str, str]]:
	"""(table fieldname, child DocType) for each child table with encrypted fields."""
	return [
		(df.fieldname, df.options) for df in frappe.get_meta(doctype).get_table_fields() if df.options in encrypted_fields()
	]


class EncryptedFieldsMixin:
	"""Encrypts `ENCRYPTED_FIELDS` on the way to the database and decrypts them on the way back.

	Child rows are loaded by their parent without their own `load_from_db`, so the parent
	decrypts them; their writes go through their own controller, which also takes this mixin.
	"""

	def load_from_db(self):
		super().load_from_db()
		self.decrypt_fields()
		for table_field, _child in get_encrypted_tables(self.doctype):
			for row in self.get(table_field):
				row.decrypt_fields()
		return self

	def db_insert(self, *args, **kwargs):
		with self.fields_encrypted():
			return super().db_insert(*args, **kwargs)

	def db_update(self):
		with self.fields_encrypted():
			return super().db_update()

	def decrypt_fields(self):
		for field in encrypted_fields().get(self.doctype, ()):
			self.set(field, decrypt(self.get(field)))

	@contextlib.contextmanager
	def fields_encrypted(self):
		"""Hold ciphertext for the length of a write, then put the plain values back."""
		plain = {field: self.get(field) for field in encrypted_fields().get(self.doctype, ())}
		set_indexes(self)
		try:
			for field, value in plain.items():
				self.set(field, encrypt(value))
			yield
		finally:
			for field, value in plain.items():
				self.set(field, value)


def redact_version(doc, method=None):
	"""Keep encrypted values out of Frappe's Version log: it records that they changed, not to what."""
	if doc.ref_doctype not in encrypted_fields() or not doc.data:
		return
	data = frappe.parse_json(doc.data)
	if redact_changes(doc.ref_doctype, data):
		doc.data = frappe.as_json(data, indent=None)


def redact_changes(doctype: str, data: dict) -> bool:
	"""Redact a Version diff in place: changed fields, and child rows changed, added or removed."""
	fields = set(encrypted_fields().get(doctype, ())) | set(INDEX_FIELDS.get(doctype, {}).values())
	tables = dict(get_encrypted_tables(doctype))
	redacted = False

	def redact_pairs(changes, fields):
		nonlocal redacted
		for change in changes:
			if change[0] in fields:
				change[1:3] = [REDACTED if value else value for value in change[1:3]]
				redacted = True

	redact_pairs(data.get("changed") or [], fields)
	for table_field, _row, _idx, changes in data.get("row_changed") or []:
		if table_field in tables:
			redact_pairs(changes, set(encrypted_fields()[tables[table_field]]))
	for key in ("added", "removed"):
		for table_field, row in data.get(key) or []:
			for field in encrypted_fields().get(tables.get(table_field), ()):
				if row.get(field):
					row[field] = REDACTED
					redacted = True
	return redacted


def encrypt_deleted_document(doc):
	"""Leave a deleted document holding ciphertext, for the copy Frappe keeps in Deleted Document.

	Frappe writes that copy from the document in memory, with a bare `db_insert` no hook sees,
	right after `after_delete`; the deleted document's `after_delete` calls this. Restoring the
	copy decrypts as usual.
	"""
	for field in encrypted_fields().get(doc.doctype, ()):
		doc.set(field, encrypt(doc.get(field)))
	for table_field, child in get_encrypted_tables(doc.doctype):
		for row in doc.get(table_field):
			for field in encrypted_fields()[child]:
				row.set(field, encrypt(row.get(field)))


def encrypt_record(doctype: str, data: dict, transform) -> bool:
	"""Apply `transform` to the encrypted fields of a document's dict and its child rows."""
	touched = False
	for field in encrypted_fields().get(doctype, ()):
		if (new := transform(data.get(field))) != data.get(field):
			data[field] = new
			touched = True
	for table_field, child in get_encrypted_tables(doctype):
		for row in data.get(table_field) or []:
			touched = encrypt_record(child, row, transform) or touched
	return touched


def reencrypt_all(commit_every: int = 500) -> int:
	"""Bring every stored value onto the current key: encrypt plain values, re-encrypt values
	under an old key, and remake blind indexes. Returns the number of rows rewritten."""
	rewritten = 0

	def save(doctype, name, values):
		nonlocal rewritten
		frappe.db.set_value(doctype, name, values, update_modified=False)
		rewritten += 1
		if rewritten % commit_every == 0:
			frappe.db.commit()  # nosemgrep: a long job, safe to resume

	for doctype, fields in encrypted_fields().items():
		indexes = INDEX_FIELDS.get(doctype, {})
		for row in frappe.get_all(doctype, fields=["name", *fields, *indexes.values()], order_by="name"):
			values = {field: reencrypt(row[field]) for field in fields if not is_current(row[field])}
			for field, index_field in indexes.items():
				index = blind_index(field, decrypt(row[field]))
				if index != row[index_field]:
					values[index_field] = index
			if values:
				save(doctype, row.name, values)

	# The patient changelog keeps old and new values of encrypted fields, encrypted.
	for event in frappe.get_all(
		"Patient Event",
		filters={"reference_doctype": ("in", list(encrypted_fields()))},
		fields=["name", "reference_doctype", "changes"],
	):
		changes = frappe.parse_json(event.changes or "[]")
		touched = False
		for change in changes:
			if change.get("fieldname") in encrypted_fields()[event.reference_doctype]:
				for side in ("old", "new"):
					if not is_current(change.get(side)):
						change[side] = reencrypt(change[side])
						touched = True
		if touched:
			save("Patient Event", event.name, {"changes": frappe.as_json(changes, indent=None)})

	for deleted in frappe.get_all(
		"Deleted Document", filters={"deleted_doctype": ("in", list(encrypted_fields()))}, fields=["name", "deleted_doctype", "data"]
	):
		data = frappe.parse_json(deleted.data or "{}")
		if encrypt_record(deleted.deleted_doctype, data, reencrypt):
			save("Deleted Document", deleted.name, {"data": frappe.as_json(data)})

	return rewritten


def rotate() -> int:
	"""Step 2 of a key rotation (see the module docstring)."""
	rewritten = reencrypt_all()
	frappe.db.commit()
	print(f"Re-encrypted {rewritten} rows. {count_on_old_keys()} values remain under an old key.")
	return rewritten


def count_on_old_keys() -> int:
	"""Stored values not encrypted under the current key. Zero means old keys can be retired."""
	current = f"{PREFIX}{get_keys()[0].id}:%"
	return sum(
		frappe.db.count(doctype, [[field, "is", "set"], [field, "not like", current]])
		for doctype, fields in encrypted_fields().items()
		for field in fields
	)
