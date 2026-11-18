from frappe.model.document import Document

from ophis_emr_core.emr_core import phi


# The value is stored encrypted. The profile decrypts its rows as it loads them.
class PatientBillingProfileValue(phi.EncryptedFieldsMixin, Document):
	pass
