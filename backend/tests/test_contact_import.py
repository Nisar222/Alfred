import unittest

from app.contact_import import parse_contact_upload


class ContactImportTests(unittest.TestCase):
    def test_plain_text_one_number_per_line(self):
        contacts = parse_contact_upload("793555436\n+46701234567\n\n793555437\n", "numbers.txt")
        self.assertEqual([contact.phone for contact in contacts], ["793555436", "+46701234567", "793555437"])
        self.assertTrue(all(contact.name is None for contact in contacts))

    def test_plain_text_skips_header_and_comments(self):
        contacts = parse_contact_upload("phone\n793555436\n# skip this\n793555437", "numbers.txt")
        self.assertEqual([contact.phone for contact in contacts], ["793555436", "793555437"])

    def test_single_column_csv_without_header(self):
        contacts = parse_contact_upload("793555436\n793555437", "numbers.csv")
        self.assertEqual(len(contacts), 2)

    def test_single_column_csv_with_phone_header(self):
        contacts = parse_contact_upload("phone\n793555436\n793555437", "numbers.csv")
        self.assertEqual([contact.phone for contact in contacts], ["793555436", "793555437"])

    def test_legacy_csv_with_optional_name(self):
        contacts = parse_contact_upload("phone,name\n+971500000003,Noor\n", "contacts.csv")
        self.assertEqual(contacts[0].phone, "+971500000003")
        self.assertEqual(contacts[0].name, "Noor")

    def test_rejects_multi_column_without_phone_header(self):
        with self.assertRaises(ValueError):
            parse_contact_upload("name,details\nAisha,Retail\n", "contacts.csv")


if __name__ == "__main__":
    unittest.main()
