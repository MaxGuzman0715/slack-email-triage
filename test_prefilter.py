"""Check the prefilter against real subject lines. No API key needed.

Cases are taken from the inbox and Interview_slots screenshots.
  KEEP = must reach the model (dropping it is the expensive mistake)
  DROP = should be killed by rules, for free
  CONF = an application confirmation: reaches the model by default, and is
         dropped only when DROP_CONFIRMATIONS=true
"""

import os

from prefilter import prefilter

CASES = [
    # --- from Interview_slots: all of these MUST survive ---
    ("KEEP", "scott@levio.com", "Levio: Interview with our team", ""),
    ("KEEP", "victoria@aeq.com", "Interview with AEQ", ""),
    ("KEEP", "hr@vertohealth.com", "Verto Health | Next Steps", ""),
    ("KEEP", "dan@toolbx.com", "Next steps at TOOLBX", ""),
    ("KEEP", "taha@firm.com", "Let's get a phone interview booked", ""),
    ("KEEP", "victoria@aequilibrium.com", "Let's Have a Chat! | Aequilibrium", ""),
    ("KEEP", "liza@zoominfo.com", "ZoomInfo Video Interview", ""),
    ("KEEP", "quest@icims.com", "Scheduling Your First Meeting - Assessment", ""),
    ("KEEP", "jezell@circleci.com", "Ready to talk CircleCI? Schedule below", ""),
    ("KEEP", "anitha@corp.com", "Hansal, you're invited to an interview", ""),
    ("KEEP", "vf@corp.com", "Next Steps | Senior Software Developer", ""),
    ("KEEP", "binjeti@evergreentechnologies.com",
     "Data Engineer - Senior | Direct Client Opportunity",
     "I came across your profile while recruiting for Data Engineer - Senior. "
     "I'd be happy to connect and discuss it further."),

    # --- invites that never say "interview" ---
    ("KEEP", "sarah@co.com", "Quick question", "Would Tuesday or Thursday work for a quick chat?"),
    ("KEEP", "mike@co.com", "Following up", "Are you available for a 30 min call this week?"),
    ("KEEP", "no-reply@greenhouse.io", "Your upcoming conversation",
     "Please use the link below to pick a time: https://calendly.com/x/30min"),

    # --- from the noisy inbox: these should die for free ---
    ("CONF", "noreply@asics.com", "Thank you for applying to ASICS", ""),
    ("CONF", "noreply@myworkday.com", "Harris Workday Notification",
     "Dear Rajeev, Thank you very much for your application."),
    ("CONF", "no-reply@us.greenhouse-mail.io", "Thanks for applying to Lime", ""),
    ("CONF", "talent@render.com", "Application Received — Senior Engineer", ""),
    ("CONF", "hiring@kin.com", "Rajeev thank you for applying", ""),
    ("CONF", "noreply@clarity.com", "Thanks for your application to Clarity", ""),
    ("DROP", "jobalerts-noreply@linkedin.com", "LinkedIn Job Alerts", ""),
    ("DROP", "alerts@jobright.com", "12 new jobs for you", ""),
    ("DROP", "news@techcrunch.com", "TechCrunch Daily Newsletter", ""),

    # --- the trap that matters most: a live thread that says "unfortunately".
    #     These MUST survive. Dropping one is a missed interview. ---
    ("KEEP", "sarah@co.com", "Re: Tuesday",
     "Unfortunately I can't do Tuesday, would Wednesday work instead?"),
    ("KEEP", "hr@co.com", "Need to reschedule",
     "Unfortunately the hiring manager is out. Can we find a new time?"),
    ("KEEP", "hr@co.com", "Our call", "Unfortunately the slot moved. Another time this week?"),
    ("KEEP", "hr@co.com", "Great news",
     "Thanks for your interest. We would like to extend you an offer."),
    ("KEEP", "hr@co.com", "Invite", "Calendar invite attached for your panel."),
    ("KEEP", "hr@co.com", "Chat", "Following up on our conversation - are you free Thursday?"),

    # --- screening questions: a live process, just not a scheduling one.
    #     The "thank you for your interest" wrapper must not drop these. ---
    ("KEEP", "r@co.com", "Re: Senior Engineer",
     "Thank you for your interest. What is your expected hourly rate?"),
    ("KEEP", "r@co.com", "Quick question", "Do you hold an active security clearance?"),
    ("KEEP", "r@co.com", "Checking", "Are you authorized to work in Canada without sponsorship?"),
    ("KEEP", "r@co.com", "Timing", "What is your notice period with your current employer?"),
    ("KEEP", "r@co.com", "Still there?", "Are you still interested in this position?"),
    ("KEEP", "r@co.com", "One more thing", "Could you send an updated resume in Word format?"),
    ("KEEP", "r@co.com", "Paperwork", "Please complete the attached candidate intake form."),
    ("KEEP", "r@co.com", "Final steps", "Please complete your background check to proceed."),
    ("KEEP", "r@co.com", "Good news", "We would like to move you to the next round."),

    # --- real misses caught in production, 26 Sep. A polite opener was
    #     killing emails that then asked for something. ---
    ("KEEP", "interviews@instahire.com",
     "Finish your Applied AI Engineer interview when you're ready",
     "Thanks for applying to the Applied AI Engineer role at Elevation Capital. "
     "Open the link below to pick up where you left off. We'd love to have your "
     "interview back by Tuesday, Sep 29."),
    ("KEEP", "hr-akamaijobs@akamai.com", "Action Needed Regarding Your Akamai Application",
     "Thank you for your application. Just one more step before you get started. "
     "You must confirm your identity using the one-time passcode: 402249"),
    ("KEEP", "no-reply@micro1.ai", "Final reminder to complete your job application",
     "This is a final reminder to complete your AI interview."),

    # --- mechanical noise: no ask, safe to drop ---
    ("DROP", "no-reply@adp.com", "Here is your verification code from ADP",
     "Your code is 883021. It expires in 10 minutes."),
    ("DROP", "no-reply@mercor.com", "Sign in to Mercor", "Click to sign in."),
    ("DROP", "no-reply@linkedin.com", "Your profile is popular - 14 search appearances",
     "See who viewed your profile."),
    ("DROP", "billing@x.com", "Your receipt from Acme", "Invoice attached."),

    # --- the same shapes, but WITH an ask: must survive. These prove the
    #     noise rules stay subordinate to the interview signals. ---
    ("KEEP", "hr@co.com", "Your verification code",
     "Use this code to sign in and then schedule your interview."),
    ("KEEP", "hr@qualcomm.com", "Welcome to Qualcomm Careers",
     "Welcome! Please complete your profile to continue your application."),
    ("KEEP", "r@co.com", "Invitation to connect",
     "I'd like to connect about a Senior Engineer role - are you available for a chat?"),

    # --- rejections, including the trap that mentions interviewing ---
    ("DROP", "hr@co.com", "Update on your application",
     "Thanks for interviewing with us last week. Unfortunately we are moving "
     "forward with other candidates."),
    ("DROP", "hr@co.com", "Your application to Stripe",
     "We have decided to proceed with other candidates at this time."),
    ("DROP", "hr@co.com", "Role update", "The position has been filled."),
]

strict = os.environ.get("DROP_CONFIRMATIONS", "").lower() in ("1", "true", "yes")
print("mode:", "strict (confirmations dropped)" if strict else "default (confirmations -> model)")

fails = 0
for want, sender, subject, body in CASES:
    reason = prefilter(sender, subject, body)
    got = "DROP" if reason else "KEEP"
    expect = ("DROP" if strict else "KEEP") if want == "CONF" else want
    ok = got == expect
    fails += not ok
    note = f"  ({reason})" if reason else ""
    print(f"{'ok  ' if ok else 'FAIL'} {got:4} {subject[:52]:52}{note}")

dropped = sum(1 for w, s_, su, b in CASES if prefilter(s_, su, b))
print()
print(f"{len(CASES) - fails}/{len(CASES)} correct  |  {dropped}/{len(CASES)} dropped by rules, no API call")
raise SystemExit(1 if fails else 0)
