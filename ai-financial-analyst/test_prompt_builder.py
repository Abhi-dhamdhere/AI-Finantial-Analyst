from pdf_parser import extract_text_from_pdf
from prompt_builder import build_prompt

file_path = "C:/Users/dhamd/Desktop/Project/BharatFin AI/ai-financial-analyst/HDFCQ2.pdf"

text = extract_text_from_pdf(file_path)
prompt = build_prompt(text)

print(prompt[:2000])  # preview prompt