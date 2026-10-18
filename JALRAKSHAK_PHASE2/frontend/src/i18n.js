// English / Hindi. The English text is the key, so screens stay readable and any string without a Hindi
// entry simply shows in English. Covered: navigation, sign-in, the organisation (user) panel, privacy and
// two-factor screens. The administrators' working screens are English only (see docs/compliance.md).
import { createContext, createElement, useCallback, useContext, useMemo, useState } from "react";

const HI = {
  // navigation and shell
  "Home": "मुख्य पृष्ठ", "My requests": "मेरे अनुरोध", "Report a problem": "समस्या बताएं", "Profile": "प्रोफ़ाइल",
  "Notifications": "सूचनाएँ", "Mark all as read": "सभी को पढ़ा हुआ करें", "Sign out": "साइन आउट",
  "Profile and password": "प्रोफ़ाइल और पासवर्ड", "Signed in as": "साइन इन:", "Administrator": "प्रशासक",
  "User": "उपयोगकर्ता", "Admin": "प्रशासन", "Language": "भाषा",
  "Nothing yet. You'll see plan decisions and replies here.": "अभी कुछ नहीं। योजना के निर्णय और जवाब यहाँ दिखेंगे।",
  "Cannot load data.": "डेटा लोड नहीं हो सका।", "Loading…": "लोड हो रहा है…",
  // sign-in
  "Sign in": "साइन इन करें", "Email": "ईमेल", "Password": "पासवर्ड", "Signing in…": "साइन इन हो रहा है…",
  "New organisation? Request an account": "नई संस्था? खाते के लिए आवेदन करें",
  "Two-factor sign-in": "दो-चरणीय साइन इन",
  "Enter the 6-digit code from your authenticator app.": "अपने ऑथेंटिकेटर ऐप से 6 अंकों का कोड डालें।",
  "Code": "कोड", "Verify": "सत्यापित करें", "Back": "वापस",
  "I have read and accept the privacy notice": "मैंने गोपनीयता सूचना पढ़ ली है और स्वीकार करता/करती हूँ",
  "Privacy notice": "गोपनीयता सूचना", "Close": "बंद करें",
  // home
  "Request water": "पानी का अनुरोध करें", "set by the water department.": "जल विभाग द्वारा निर्धारित।",
  "Priority": "प्राथमिकता", "of 5": "/ 5",
  "Waiting for a decision": "निर्णय की प्रतीक्षा में", "of at most {n} at a time": "एक समय में अधिकतम {n}",
  "Water received": "प्राप्त पानी", "Share delivered": "प्राप्त हिस्सा", "Open issues": "खुली समस्याएँ",
  "of {x} asked in decided requests": "तय हुए अनुरोधों में माँगे गए {x} में से",
  "{a} full · {b} partly · {c} not served": "{a} पूरा · {b} आंशिक · {c} नहीं मिला",
  "{n} unread notifications": "{n} अपठित सूचनाएँ",
  "Available now": "अभी उपलब्ध", "Waiting requests (whole city)": "प्रतीक्षारत अनुरोध (पूरा शहर)",
  "Expected use today": "आज का अनुमानित उपयोग", "Alerts for your area": "आपके क्षेत्र की चेतावनियाँ",
  "No alerts for {zone} right now.": "अभी {zone} के लिए कोई चेतावनी नहीं।",
  "There is enough water for every waiting request.": "हर प्रतीक्षारत अनुरोध के लिए पर्याप्त पानी है।",
  "Water is short: not every request can be served in full.": "पानी कम है: हर अनुरोध पूरा नहीं हो सकता।",
  "Water is very short: requests ask for far more than is available.": "पानी बहुत कम है: माँग उपलब्धता से कहीं अधिक है।",
  "Piped supply to your zone": "आपके क्षेत्र में नल से जलापूर्ति",
  "No supply timetable has been published yet.": "अभी कोई जलापूर्ति समय-सारणी जारी नहीं हुई है।",
  "about {n} litres per person (norm {m})": "लगभग {n} लीटर प्रति व्यक्ति (मानक {m})",
  "Timetable for {d}": "{d} की समय-सारणी", "No piped supply in this timetable.": "इस समय-सारणी में नल से आपूर्ति नहीं।",
  "Water policy": "जल नीति", "version {v}": "संस्करण {v}",
  // requests
  "Litres needed": "आवश्यक लीटर", "Urgency": "तात्कालिकता", "Needed within (minutes)": "कितने मिनट में चाहिए",
  "Note for the water department (optional)": "जल विभाग के लिए टिप्पणी (वैकल्पिक)",
  "Send request": "अनुरोध भेजें", "Sending…": "भेजा जा रहा है…",
  "Urgency and the time you need it by both raise your request's place in the Water-aware plan. Your organisation's priority is set by the department.":
    "तात्कालिकता और समय-सीमा दोनों जल-सजग योजना में आपके अनुरोध का स्थान बढ़ाते हैं। आपकी संस्था की प्राथमिकता विभाग तय करता है।",
  "Your requests": "आपके अनुरोध", "Refresh": "ताज़ा करें", "No requests yet.": "अभी कोई अनुरोध नहीं।",
  "Use the form to ask for water.": "पानी माँगने के लिए फ़ॉर्म भरें।",
  "Asked": "माँगा", "Sent": "भेजा", "Status": "स्थिति", "Received": "प्राप्त", "Cancel": "रद्द करें",
  "Yes, cancel": "हाँ, रद्द करें", "No water": "पानी नहीं मिला", "urgency {n}": "तात्कालिकता {n}",
  "Request #{id} sent. You'll be notified when a plan decides it.": "अनुरोध #{id} भेजा गया। निर्णय होने पर सूचना मिलेगी।",
  "Request #{id} cancelled.": "अनुरोध #{id} रद्द किया गया।",
  "You have {n} requests waiting, the most allowed. Cancel one or wait for the next plan.":
    "आपके {n} अनुरोध प्रतीक्षा में हैं, जो अधिकतम सीमा है। एक रद्द करें या अगली योजना की प्रतीक्षा करें।",
  "Pending": "प्रतीक्षारत", "Approved": "स्वीकृत", "Partly served": "आंशिक रूप से दिया गया", "Rejected": "अस्वीकृत",
  "Cancelled": "रद्द", "1 – can wait": "1 – रुक सकते हैं", "2 – low": "2 – कम", "3 – normal": "3 – सामान्य",
  "4 – high": "4 – अधिक", "5 – critical": "5 – अत्यंत आवश्यक",
  // issues
  "What kind of problem?": "किस प्रकार की समस्या?", "Short title": "छोटा शीर्षक", "Where (optional)": "कहाँ (वैकल्पिक)",
  "What is happening?": "क्या हो रहा है?", "Send report": "रिपोर्ट भेजें", "Your reports": "आपकी रिपोर्ट",
  "Leak": "रिसाव", "No supply": "आपूर्ति नहीं", "Low pressure": "कम दबाव", "Water quality": "पानी की गुणवत्ता", "Other": "अन्य",
  "Open": "खुली", "Being worked on": "कार्य जारी", "Resolved": "हल हुई", "Closed": "बंद",
  "Water department:": "जल विभाग:", "No reply yet.": "अभी कोई जवाब नहीं।",
  "You haven't reported anything.": "आपने अभी कुछ रिपोर्ट नहीं किया।",
  "Issue #{id} reported. The water department has been notified.": "समस्या #{id} दर्ज हुई। जल विभाग को सूचित किया गया।",
  // profile, privacy, 2FA
  "Your profile": "आपकी प्रोफ़ाइल", "Full name": "पूरा नाम", "Phone": "फ़ोन", "Save profile": "प्रोफ़ाइल सहेजें",
  "Role": "भूमिका", "Organisation": "संस्था", "Last sign-in": "पिछला साइन इन", "Profile saved.": "प्रोफ़ाइल सहेजी गई।",
  "My data & privacy": "मेरा डेटा और गोपनीयता",
  "Download my data": "मेरा डेटा डाउनलोड करें", "Ask to erase my account": "मेरा खाता मिटाने का अनुरोध करें",
  "Ask for a correction": "सुधार का अनुरोध करें", "Raise a grievance": "शिकायत दर्ज करें",
  "Details": "विवरण", "Send": "भेजें", "Your privacy requests": "आपके गोपनीयता अनुरोध",
  "Please accept the updated privacy notice to keep using the portal.":
    "पोर्टल का उपयोग जारी रखने के लिए कृपया अद्यतन गोपनीयता सूचना स्वीकार करें।",
  "Read and accept": "पढ़ें और स्वीकार करें", "Accept": "स्वीकार करें",
  "Turn on two-factor sign-in": "दो-चरणीय साइन इन चालू करें", "Turn off": "बंद करें",
  "Two-factor sign-in is on.": "दो-चरणीय साइन इन चालू है।", "Two-factor sign-in is off.": "दो-चरणीय साइन इन बंद है।",
  "Add this key to an authenticator app, then enter the code it shows.":
    "यह कुंजी किसी ऑथेंटिकेटर ऐप में जोड़ें, फिर उसमें दिखा कोड डालें।",
  "Change password": "पासवर्ड बदलें",
};

const LangContext = createContext({ lang: "en", setLang: () => {}, t: (s) => s });

function fill(text, vars) {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
}

export function translate(lang, text, vars) {
  return fill(lang === "hi" ? HI[text] || text : text, vars);
}

function readLang() {
  try {
    return localStorage.getItem("jal-lang") === "hi" ? "hi" : "en";
  } catch {
    return "en";
  }
}

export function LangProvider({ children, onChange }) {
  const [lang, setLangState] = useState(readLang);
  const setLang = useCallback((l) => {
    setLangState(l);
    try { localStorage.setItem("jal-lang", l); } catch { /* ignore */ }
    document.documentElement.lang = l;
    if (onChange) onChange(l);
  }, [onChange]);
  const value = useMemo(() => ({ lang, setLang, t: (s, v) => translate(lang, s, v) }), [lang, setLang]);
  return createElement(LangContext.Provider, { value }, children);
}

export const useLang = () => useContext(LangContext);
export const useT = () => useContext(LangContext).t;
