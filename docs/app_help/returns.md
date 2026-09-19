# Returns

Three sub-pages: Sales Return, Purchase Return, Return / Write-off.

## Sales Return (inline steps)
1. Find bill: Bill No/Customer OR Medicine (medicine limited to last N days)
2. Load Bill, pick medicine, Return Qty, Add to Return
3. Items to Return list — Remove Selected
4. Reason, Discount %, Refund Amount — Save Return (F5) restores stock

## Purchase Return (inline + qty dialog)
Load Purchase, select item, Add Selected opens Return — {medicine} qty dialog.
Save Return (F5) reduces stock. Label: Credit to Supplier.
Saved returns in history cannot be edited — Load Purchase is verify only.

## Write-off (stock disposal)
Supplier tabs, add medicines/batches, reason dropdown, Submit this supplier.
Lines with purchase record go to return; without record go to write-off.
