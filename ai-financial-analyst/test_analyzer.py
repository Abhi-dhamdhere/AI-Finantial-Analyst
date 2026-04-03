from pdf_parser import extract_text_from_pdf
from prompt_builder import build_prompt
from analyzer import analyze_financials
from kpi_extractor import extract_kpis

file_path = "C:/Users/dhamd/Desktop/Project/BharatFin AI/ai-financial-analyst/HDFCQ2.pdf"

text = extract_text_from_pdf(file_path)

# NEW STEP
kpis = extract_kpis(text)

prompt = build_prompt(text, kpis)

result = analyze_financials(prompt)

print("\n===== AI FINANCIAL ANALYSIS =====\n")
print(result)