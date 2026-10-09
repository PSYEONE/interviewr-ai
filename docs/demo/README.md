# Demo documents

Fictional CV and job description, for recording screenshots and GIFs without putting real
personal details in the repo. Every name, company and email here is made up; `example.com` is
reserved by the IETF for exactly this.

Load them from the dashboard under Context, or:

```bash
curl -s -F file=@docs/demo/resume.md \
  -H "Authorization: Bearer $INTERVIEWR_AUTH_TOKEN" \
  http://127.0.0.1:8765/documents
```

They are written to pair with each other: the CV answers most of what the job description
asks for, so retrieval has something to find and the demo answers look like the real thing.
