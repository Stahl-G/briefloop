# Bundled runtime dependency

This directory contains the MIT-licensed WikiSkill wheel required by BriefLoop. It is a built dependency, not a second source checkout. `start.sh` installs it automatically, so users clone only BriefLoop.

Source: Stahl-G/wikiskill, based on 9df975b with the feedback-loop extension, version 0.1.1+briefloop.2. Licenses and notices are included in the wheel metadata. Developers rebuild from the independent WikiSkill repository when updating the dependency; end users do not need that checkout.
