from frappe.model.document import Document

from ophis_emr_core.emr_core import phi


# The phone number is stored encrypted. The patient decrypts its rows as it loads them.
class PatientEmergencyContact(phi.EncryptedFieldsMixin, Document):
	pass
