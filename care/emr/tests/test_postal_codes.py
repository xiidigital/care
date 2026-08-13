from django.test import SimpleTestCase

from care.emr.geography.postal_codes import validate_postal_code


class PostalCodeValidationTestCase(SimpleTestCase):
    def test_validates_mexican_postal_code(self):
        self.assertEqual(validate_postal_code("76000", "MX"), "76000")

    def test_preserves_zip_plus_four(self):
        self.assertEqual(validate_postal_code("12345-6789", "US"), "12345-6789")

    def test_rejects_invalid_country_specific_format(self):
        with self.assertRaisesRegex(ValueError, "5 dígitos"):
            validate_postal_code("7600", "MX")
