---------------- MODULE CleanupDesign ----------------
EXTENDS Integers, TLC
CONSTANT UnsafePolicy
VARIABLES phase, known, owned, busy, protected, closed, generation, candidate, signalled, facts
vars == <<phase, known, owned, busy, protected, closed, generation, candidate, signalled, facts>>
Eligible == known /\ owned /\ ~busy /\ ~protected
Init == /\ phase = "observe" /\ known \in BOOLEAN /\ owned \in BOOLEAN
        /\ busy \in BOOLEAN /\ protected \in BOOLEAN /\ closed = FALSE
        /\ generation \in {0,1} /\ candidate = -1 /\ signalled = FALSE
        /\ facts = <<FALSE,FALSE,FALSE,FALSE,FALSE,FALSE>>
Observe == /\ phase = "observe" /\ phase' = IF Eligible THEN "review" ELSE "protect"
           /\ candidate' = IF Eligible THEN generation ELSE -1
           /\ UNCHANGED <<known,owned,busy,protected,closed,generation,signalled,facts>>
Drain == /\ phase = "review" /\ Eligible /\ candidate = generation
         /\ phase' = "drain" /\ closed' = TRUE
         /\ UNCHANGED <<known,owned,busy,protected,generation,candidate,signalled,facts>>
Stop == /\ ((phase = "drain" /\ Eligible /\ closed /\ candidate = generation)
             \/ (UnsafePolicy /\ phase = "protect" /\ ~busy))
        /\ phase' = "stopped" /\ signalled' = TRUE
        /\ facts' = <<known,owned,~busy,~protected,closed,candidate = generation>>
        /\ UNCHANGED <<known,owned,busy,protected,closed,generation,candidate>>
EvidenceChanges == /\ phase # "stopped"
                   /\ known' \in BOOLEAN /\ owned' \in BOOLEAN /\ busy' \in BOOLEAN
                   /\ protected' \in BOOLEAN /\ generation' \in {0,1}
                   /\ <<known',owned',busy',protected',generation'>> # <<known,owned,busy,protected,generation>>
                   /\ phase' = "protect" /\ candidate' = -1 /\ closed' = FALSE
                   /\ UNCHANGED <<signalled,facts>>
Refresh == /\ phase = "protect" /\ phase' = "observe"
           /\ UNCHANGED <<known,owned,busy,protected,closed,generation,candidate,signalled,facts>>
Done == /\ phase = "stopped" /\ UNCHANGED vars
Next == Observe \/ Drain \/ Stop \/ EvidenceChanges \/ Refresh \/ Done
NoUntrackedStop == signalled => facts[1]
NoUnownedStop == signalled => facts[2]
NoBusyStop == signalled => facts[3]
NoProtectedStop == signalled => facts[4]
AdmissionClosed == signalled => facts[5]
NoReusedPidStop == signalled => facts[6]
Spec == Init /\ [][Next]_vars
======================================================
