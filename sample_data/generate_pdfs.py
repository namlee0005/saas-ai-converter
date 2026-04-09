from fpdf import FPDF

def make_pdf(filename, title, body_lines):
    pdf = FPDF()
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(w=190, h=10, text=title, new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.ln(10)
    pdf.set_font("helvetica", size=12)
    for line in body_lines:
        pdf.multi_cell(w=190, h=8, text=line)
    pdf.output(filename)

make_pdf("/home/ben/project/projects/saas-ai-converter/sample_data/1_Immigration_Consulting.pdf", "Maple Leaf Visa Solutions", [
    "Company Overview:",
    "We specialize in Canadian immigration and study abroad services.",
    "",
    "Services & Fees:",
    "1. Express Entry: Requirements include IELTS 7.0+ and a Bachelor's degree. Processing time: 6 months. Consulting Fee: $3,000.",
    "2. Startup Visa: Requires an innovative business plan and a letter of support from a designated organization. Processing time: 12-16 months. Consulting Fee: $10,000.",
    "",
    "Frequently Asked Questions:",
    "- Can I bring my family? Yes, your spouse and dependent children can accompany you.",
    "- How to book an appointment? Please provide your name and email in the chat to schedule a consultation with our experts."
])

make_pdf("/home/ben/project/projects/saas-ai-converter/sample_data/2_Perfect_Smile_Dental.pdf", "Perfect Smile Dental Clinic", [
    "Clinic Overview:",
    "Top-tier cosmetic dentistry with international standards.",
    "",
    "Services & Pricing:",
    "1. Porcelain Veneers: $200 - $500 per tooth. Includes a 10-year warranty.",
    "2. Dental Implants: $1000 - $2000 per tooth. Includes a lifetime warranty.",
    "3. Invisalign Clear Aligners: $3,000 - $5,000 per treatment. Duration: 12-18 months.",
    "",
    "Frequently Asked Questions:",
    "- Do you offer installment plans? Yes, we offer 0% interest installment plans for up to 12 months.",
    "- Does it hurt? We use modern local anesthesia ensuring a pain-free experience.",
    "- How to book a checkup? Just drop your phone number and email, our AI will schedule it directly into the doctor's calendar."
])

make_pdf("/home/ben/project/projects/saas-ai-converter/sample_data/3_Diamond_Bay_Real_Estate.pdf", "Diamond Bay Luxury Villas", [
    "Project Overview:",
    "An exclusive enclave of 5-star beachfront villas featuring a private marina and world-class amenities.",
    "",
    "Pricing & Villa Types:",
    "1. 3-Bedroom Ocean View Villa: $1.5M. Area: 300 sqm.",
    "2. 5-Bedroom Signature Villa with Private Beach: $3.0M. Area: 600 sqm.",
    "",
    "Amenities:",
    "- Private infinity pools for each villa.",
    "- 18-hole championship golf course access.",
    "- 24/7 VIP concierge and security.",
    "",
    "Payment Plan:",
    "- Booking Fee: $20,000.",
    "- 1st Installment: 20% down payment upon signing the contract.",
    "- Final Installment: 80% upon handover in Q4 2026.",
    "- Site visit booking: Leave your details and our AI will arrange a VIP tour for you."
])
print("PDFs generated successfully!")
