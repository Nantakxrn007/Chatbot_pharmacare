// ชื่อยา/ตัวยาสำคัญทั้งหมดที่คำตอบอ้างอิงได้ -- ใช้ทำ highlight ชื่อยาให้อ่านง่าย
// (ตาม feedback: เดิม highlight เฉพาะ "ขนาดยา" ส่วนชื่อยาเป็นตัวอักษรธรรมดา กวาดตาหาไม่เจอ)
//
// ที่มา (ชุดข้อมูลปิด -- โปรเจกต์นี้ไม่ ingest ข้อมูลเพิ่ม): ชื่อยาในตาราง Dose
// + ตัวยาสำคัญของผลิตภัณฑ์ที่ brand gateway เติมให้ + ชื่อยาปฏิชีวนะที่ AAFP/URI อ้างถึง
// (backend/symptomatic_gateway.py: load_formulary() / _brand_entries() / _ATB_NAMES)
// + rag/data/drugs.json  -- ถ้าตาราง Dose เปลี่ยน ให้ปรับรายการนี้ตามแหล่งข้างต้น
//
// เรียงจากชื่อยาวไปสั้น เพื่อให้ alternation จับชื่อเต็มก่อนชื่อย่อย
// (เช่น "Amoxicillin/clavulanate" ต้องชนะ "Amoxicillin")
export const DRUG_NAMES: string[] = [
  'Beclometasone dipropionate',
  'Difflam forte throat spray',
  'Decolgen prin / TIFFY DEY',
  'Chlorpheniramine maleate',
  'Amoxicillin/clavulanate',
  'Penicillin G benzathine',
  'Piperacillin-tazobactam',
  'Triamcinolone acetonide',
  'Fluticasone propionate',
  'Strepsils chesty cough',
  'Betadine gargle 30 ml',
  'Betadine throat spray',
  'Glyceryl guaiacolate',
  'Propoliz mouth spray',
  'Fluticasone furoate',
  'Strepsils dry cough',
  'Strepsils Maxipluzz',
  'Hydroxychloroquine',
  'Mometasone furoate',
  'Kamilosan M spray',
  'Phenylephrine HCl',
  'Chlorpheniramine',
  'Dextromethorphan',
  'Difflam Lozenges',
  'Levodropropizine',
  'N-Acetylcysteine',
  'Strepsils Maxpro',
  'Benzydamine HCl',
  'Betadine gargle',
  'Brompheniramine',
  'Diphenhydramine',
  'Naphazoline HCl',
  'Povidone-iodine',
  'Acetylcysteine',
  'Amylmetacrecol',
  'Clarithromycin',
  'Cyproheptadine',
  'Levocetirizine',
  'Mefenamic acid',
  'Solmax capsule',
  'Terpin hydrate',
  'Xylometazoline',
  'Carbocysteine',
  'Decolgen prin',
  'Desloratadine',
  'Metronidazole',
  'Oxymetazoline',
  'Phenylephrine',
  'Roxithromycin',
  'Strepsils HHR',
  'Ambroxol HCl',
  'Azithromycin',
  'Erythromycin',
  'Fexofenadine',
  'flurbiprofen',
  'Levofloxacin',
  'Penicillin G',
  'Penicillin V',
  'Amoxicillin',
  'Cefpodoxime',
  'Ceftriaxone',
  'Ciclesonide',
  'Clavulanate',
  'Clindamycin',
  'Doxycycline',
  'Hydroxyzine',
  'Naphazoline',
  'Paracetamol',
  'Bromhexine',
  'Budesonide',
  'Cefditoren',
  'Cefotaxime',
  'Ceftibuten',
  'Cefuroxime',
  'Cephalexin',
  'Cetirizine',
  'Diclofenac',
  'Etoricoxib',
  'Lignocaine',
  'Loratadine',
  'Penicillin',
  'Vancomycin',
  'Augmentin',
  'Bilastine',
  'Celecoxib',
  'Chamomile',
  'Ibuprofen',
  'Piroxicam',
  'TIFFY DEY',
  'Ambroxol',
  'Cefdinir',
  'Cefixime',
  'Naproxen',
  'Aspirin',
  'Muclear',
  'Terco-D',
];

const RE_SPECIALS = /[.*+?^${}()|[\]\\]/g;

// ช่องว่างในชื่อยา ("Kamilosan M spray", "Penicillin V") ต้องยอมให้เป็นเว้นวรรคหลายช่อง
// หรือขึ้นบรรทัดใหม่ได้ เพราะ markdown ห่อบรรทัดเอง
const toPattern = (name: string) =>
  name.replace(RE_SPECIALS, '\\$&').replace(/(?:\\ |\s)+/g, '\\s+');

// ขอบซ้าย/ขวาเป็น lookaround ของอักษรละติน (ไม่ใช่ \b) เพราะ \b มองเฉพาะ ASCII word char
// จึงเพี้ยนเมื่อชื่อยาติดกับอักษรไทย เช่น "ยาParacetamol" หรือ "Paracetamolขนาด"
// อนุญาต "s" ต่อท้าย (พหูพจน์ เช่น "Penicillins") ให้ถูก highlight รวมไปด้วย
export const DRUG_NAME_PATTERN = new RegExp(
  `(?<![A-Za-z])(?:${DRUG_NAMES.map(toPattern).join('|')})s?(?![A-Za-z])`,
  'gi'
);
