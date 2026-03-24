#!/bin/bash
# keystone_budget.sh — Check remaining time and budget for this agent session.
# Exits non-zero if time or budget is exhausted.

OVER_BUDGET=0
NOW=$(date +%s)

# Time remaining
if [ -n "$AGENT_TIME_DEADLINE" ]; then
  REMAINING_SECS=$((AGENT_TIME_DEADLINE - NOW))
  if [ "$REMAINING_SECS" -le 0 ]; then
    echo "Remaining time: 0 seconds (OVER TIME)"
    OVER_BUDGET=1
  else
    echo "Remaining time: ${REMAINING_SECS} seconds"
  fi
else
  echo "Remaining time: unknown (AGENT_TIME_DEADLINE not set)"
fi

# Budget remaining
if [ -n "$AGENT_BUDGET_CAP_USD" ] && [ -n "$CCUSAGE_COMMAND" ]; then
  CURRENT_COST=$($CCUSAGE_COMMAND session --json 2>/dev/null \
    | jq -r '(.sessions[0].totalCost // 0)')
  if [ $? -eq 0 ] && [ -n "$CURRENT_COST" ]; then
    REMAINING=$(echo "$AGENT_BUDGET_CAP_USD - $CURRENT_COST" | bc -l)
    # Check if over budget (bc returns 1 for true, 0 for false)
    IS_OVER=$(echo "$REMAINING <= 0" | bc -l)
    if [ "$IS_OVER" -eq 1 ]; then
      printf "Remaining budget: %.2f USD (OVER BUDGET)\n" "$REMAINING"
      OVER_BUDGET=1
    else
      printf "Remaining budget: %.2f USD\n" "$REMAINING"
    fi
  else
    echo "Remaining budget: unknown (ccusage failed)"
  fi
else
  echo "Remaining budget: unknown (AGENT_BUDGET_CAP_USD or CCUSAGE_COMMAND not set)"
fi

exit $OVER_BUDGET
