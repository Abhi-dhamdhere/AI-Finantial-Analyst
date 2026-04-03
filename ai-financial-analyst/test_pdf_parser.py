from pdf_parser import extract_text_from_pdf

# Put any sample PDF in your folder
file_path = "C:/Users/dhamd/Desktop/Project/BharatFin AI/ai-financial-analyst/HDFCQ2.pdf"

text = extract_text_from_pdf(file_path)

print(text[:2000])  # print first 2000 characters