"""Prompts kept outside views and persistence code to protect API boundaries."""


RESUME_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "personal_information": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
            },
            "required": ["name", "email", "phone"],
            "additionalProperties": False,
        },
        "name": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "experience": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "education": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "projects": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "certifications": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "achievements": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "languages": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
    },
    "required": [
        "personal_information", "name", "email", "phone", "summary", "skills", "experience",
        "education", "projects", "certifications", "achievements", "languages",
    ],
    "additionalProperties": False,
}


JOB_DESCRIPTION_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "required_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "preferred_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "technologies": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "frameworks": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "databases": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "cloud_technologies": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "tools": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "soft_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "responsibilities": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "experience_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "education_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "certification_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "important_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "ats_relevant_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "seniority_level": {"type": "string"},
        "other_explicit_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
    },
    "required": [
        "required_skills", "preferred_skills", "technologies", "frameworks",
        "databases", "cloud_technologies", "tools", "soft_skills",
        "responsibilities", "experience_requirements", "education_requirements",
        "certification_requirements", "important_keywords", "ats_relevant_terms",
        "seniority_level", "other_explicit_requirements",
    ],
    "additionalProperties": False,
}


GAP_ANALYSIS_REQUIREMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string"},
        "category": {"type": "string"},
        "match_status": {
            "type": "string",
            "enum": ["MATCHED", "PARTIALLY_MATCHED", "MISSING", "NOT_VERIFIABLE"],
        },
        "candidate_evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
        "recommendation": {"type": "string"},
    },
    "required": [
        "requirement", "category", "match_status", "candidate_evidence",
        "confidence", "reasoning", "recommendation",
    ],
    "additionalProperties": False,
}


GAP_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_match_percentage": {"type": "number", "minimum": 0, "maximum": 100},
        "matched_requirements": {
            "type": "array",
            "items": GAP_ANALYSIS_REQUIREMENT_SCHEMA,
            "maxItems": 80,
        },
        "partially_matched_requirements": {
            "type": "array",
            "items": GAP_ANALYSIS_REQUIREMENT_SCHEMA,
            "maxItems": 80,
        },
        "missing_requirements": {
            "type": "array",
            "items": GAP_ANALYSIS_REQUIREMENT_SCHEMA,
            "maxItems": 80,
        },
        "not_verifiable_requirements": {
            "type": "array",
            "items": GAP_ANALYSIS_REQUIREMENT_SCHEMA,
            "maxItems": 80,
        },
        "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "weaknesses_gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "important_missing_information": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "recommendations_for_profile_improvement": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "recommendation": {"type": "string"},
                    "recommendation_type": {
                        "type": "string",
                        "enum": ["MISSING_PROFILE_INFORMATION", "SKILL_TO_ACQUIRE"],
                    },
                },
                "required": ["recommendation", "recommendation_type"],
                "additionalProperties": False,
            },
            "maxItems": 20,
        },
        "ats_keyword_coverage": {
            "type": "object",
            "properties": {
                "covered_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                "missing_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                "coverage_percentage": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": [
                "covered_keywords", "missing_keywords", "coverage_percentage",
            ],
            "additionalProperties": False,
        },
        "interview_readiness_observations": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
    },
    "required": [
        "overall_match_percentage", "matched_requirements",
        "partially_matched_requirements", "missing_requirements",
        "not_verifiable_requirements", "strengths", "weaknesses_gaps",
        "important_missing_information", "recommendations_for_profile_improvement",
        "ats_keyword_coverage", "interview_readiness_observations",
    ],
    "additionalProperties": False,
}


TAILORED_RESUME_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "source_index": {"type": "integer", "minimum": 0},
        "tailored_text": {"type": "string"},
    },
    "required": ["source_index", "tailored_text"],
    "additionalProperties": False,
}


TAILORED_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "experience": {"type": "array", "items": TAILORED_RESUME_ITEM_SCHEMA, "maxItems": 20},
        "projects": {"type": "array", "items": TAILORED_RESUME_ITEM_SCHEMA, "maxItems": 15},
        "education": {"type": "array", "items": TAILORED_RESUME_ITEM_SCHEMA, "maxItems": 10},
        "certifications": {"type": "array", "items": TAILORED_RESUME_ITEM_SCHEMA, "maxItems": 15},
    },
    "required": [
        "summary", "skills", "experience", "projects", "education", "certifications",
    ],
    "additionalProperties": False,
}


# The candidate's own name/contact block is never AI-generated — it is filled
# in from verified personal_information — so it is deliberately absent here.
COVER_LETTER_SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "body_paragraphs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "closing_paragraph": {"type": "string"},
    },
    "required": [
        "greeting", "opening_paragraph", "body_paragraphs", "closing_paragraph",
    ],
    "additionalProperties": False,
}


def build_resume_extraction_prompt(resume_text):
    return """You are extracting structured information from a resume.
Do not invent information. Only extract information supported by the supplied
resume. Use null for unavailable scalar values and empty arrays for unavailable
collections. Keep items concise and preserve the original information. Return
only JSON that conforms exactly to the supplied schema.

Resume text follows:
---
%s
---""" % resume_text


def build_job_description_extraction_prompt(job_description):
    """Build the prompt used by the job curation intake API."""
    return """You are extracting structured information from a job description.
Only extract information explicitly supported by the supplied job description.
Do not invent requirements. Distinguish required and preferred requirements when
possible. Use empty arrays when a category is not present. Use an empty string
for seniority_level if the seniority is not explicit. Return only JSON that
conforms exactly to the supplied schema.

Job description follows:
---
%s
---""" % job_description


def build_gap_analysis_prompt(candidate_data, job_requirements):
    return """You are comparing verified candidate data against structured job requirements.
Use ONLY CANDIDATE_DATA as candidate truth. Never invent candidate facts.
Never convert a job requirement into a candidate skill. If CANDIDATE_DATA does
not explicitly support a skill, technology, employer, certification, education,
project, responsibility, or years of experience, classify that requirement as
MISSING or NOT_VERIFIABLE.

Separate recommendations into:
- MISSING_PROFILE_INFORMATION: information the candidate may genuinely have but
  has not provided in the profile/resume data.
- SKILL_TO_ACQUIRE: requirements the candidate does not appear to have and may
  need to learn or earn.

Return only JSON conforming exactly to the supplied schema.

CANDIDATE_DATA:
%s

JOB_REQUIREMENTS:
%s""" % (candidate_data, job_requirements)


def build_resume_curation_prompt(
    candidate_source_data,
    job_title,
    job_description,
    job_requirements,
    candidate_match_summary,
    retry_feedback=None,
):
    feedback_block = ""
    if retry_feedback:
        feedback_block = (
            "\nThe previous attempt violated the rules below and was rejected. "
            "Correct every one of these in this attempt:\n- "
            + "\n- ".join(retry_feedback)
            + "\n"
        )

    return """You are tailoring a candidate's resume for one specific job using ONLY
CANDIDATE_SOURCE_DATA as ground truth. Never invent or infer facts.

STRICT RULES:
- Use ONLY facts already present in CANDIDATE_SOURCE_DATA. Never add skills, employers,
  job titles, responsibilities, projects, certifications, education, metrics,
  technologies, dates, or years of experience that are not already there.
- You may reorder, re-emphasize, reword, and restructure content, and choose which
  skills and projects are most relevant to JOB_TITLE and JOB_REQUIREMENTS.
- "skills" must be an exact subset of CANDIDATE_SOURCE_DATA.skills, spelled exactly as
  given, reordered or filtered for relevance. Do not rename or merge skill entries and
  do not add skills only because they appear in JOB_REQUIREMENTS.
- For "experience", "projects", "education", and "certifications", every returned item
  must include the integer "source_index" of the corresponding entry in
  CANDIDATE_SOURCE_DATA and a "tailored_text" that rewrites that entry's wording and
  emphasis without adding any company, date, title, technology, or metric that is not
  already present in that same source entry.
- Never change a date, year, duration, or numeric metric from the source entry.
- The summary must be specific to JOB_TITLE but built only from CANDIDATE_SOURCE_DATA
  facts, referencing CANDIDATE_MATCH_SUMMARY only to decide emphasis, never to add facts.
%s
Return only JSON that conforms exactly to the supplied schema. Do not include any
commentary outside the JSON.

JOB_TITLE:
%s

JOB_DESCRIPTION:
%s

JOB_REQUIREMENTS:
%s

CANDIDATE_MATCH_SUMMARY:
%s

CANDIDATE_SOURCE_DATA:
%s""" % (
        feedback_block,
        job_title,
        job_description,
        job_requirements,
        candidate_match_summary,
        candidate_source_data,
    )


def build_cover_letter_prompt(
    candidate_source_data,
    job_title,
    job_description,
    job_requirements,
    candidate_match_summary,
    retry_feedback=None,
):
    feedback_block = ""
    if retry_feedback:
        feedback_block = (
            "\nThe previous attempt violated the rules below and was rejected. "
            "Correct every one of these in this attempt:\n- "
            + "\n- ".join(retry_feedback)
            + "\n"
        )

    return """You are writing a cover letter for one specific job using ONLY
CANDIDATE_SOURCE_DATA for candidate facts. Never invent or infer candidate facts.

STRICT RULES:
- Every claim about the candidate must already be present in CANDIDATE_SOURCE_DATA.
  Never add skills, employers, job titles, responsibilities, projects,
  certifications, education, metrics, technologies, dates, or years of experience
  that are not already there.
- You may refer to the role and the employer's own stated needs using wording from
  JOB_TITLE, JOB_DESCRIPTION, and JOB_REQUIREMENTS — quoting the posting back is not
  inventing a candidate fact.
- Never state or imply that the candidate has a requirement the
  CANDIDATE_MATCH_SUMMARY reports as missing. Focus on genuine strengths instead.
  Never apologise for, or draw attention to, gaps.
- Never invent the hiring manager's name, the team's name, salary expectations,
  availability dates, or any detail about the company beyond what the job text says.
- If no hiring manager name appears in the job text, the greeting must be a neutral
  form such as "Dear Hiring Manager".
- Do not write a signature, the candidate's name, or contact details — those are
  added separately from verified data.
- Write 2 to 3 body paragraphs. Be specific and concrete rather than generic; avoid
  filler such as "I am a hard worker" or "I am passionate about excellence".
%s
Return only JSON that conforms exactly to the supplied schema. Do not include any
commentary outside the JSON.

JOB_TITLE:
%s

JOB_DESCRIPTION:
%s

JOB_REQUIREMENTS:
%s

CANDIDATE_MATCH_SUMMARY:
%s

CANDIDATE_SOURCE_DATA:
%s""" % (
        feedback_block,
        job_title,
        job_description,
        job_requirements,
        candidate_match_summary,
        candidate_source_data,
    )
