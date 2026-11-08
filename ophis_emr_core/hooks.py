app_name = "ophis_emr_core"
app_title = "Ophis EMR Core"
app_publisher = "STRMLNE"
app_description = "The clinical record for Canadian family practice, without a user interface"
app_email = "dev@getophis.io"
app_license = "agpl-3.0"

required_apps = ["frappe/erpnext"]

before_install = "ophis_emr_core.install.before_install"
after_install = "ophis_emr_core.install.after_install"
before_migrate = "ophis_emr_core.install.before_migrate"
after_migrate = "ophis_emr_core.install.after_migrate"

doc_events = {
	# The patient changelog. The handlers ignore documents that do not belong to a patient.
	"*": {
		"after_insert": "ophis_emr_core.emr_core.events.on_insert",
		"on_update": "ophis_emr_core.emr_core.events.on_update",
		"on_submit": "ophis_emr_core.emr_core.events.on_submit",
		"on_trash": "ophis_emr_core.emr_core.events.on_trash",
	},
	# Encrypted patient identifiers are recorded as changed, never with their values.
	"Version": {"before_insert": "ophis_emr_core.emr_core.phi.redact_version"},
}

# Hooks this app asks other apps (see the README):
#
# emr_notify_visit     a visit needs staff: (encounter, text, nurse=None, to_physicians=False)
# emr_notify           tell these users about a document: (users, text, doctype, name)
# phi_encrypted_fields {DocType: [field, ...]} of patient information other apps encrypt
