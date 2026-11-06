---
icon: lucide/file-code
title: "Job Order"
tags:
    - usage
    - job order
---

# Job Order

The processor is driven by a **Job Order XML file** that specifies the input products, the auxiliary files and the
output path. A template is provided in the repository
([`templates/JobOrder.xml`](https://github.com/aresys-srl/s1-denoise/blob/main/templates/JobOrder.xml)).

The processor reads every file path given in the Job Order and checks that it is reachable from the current working
directory. In addition:

- the AUX files provided by the user are compared with those referenced in the manifest of each input L1 product: if
  they differ, or if an AUX file is not referenced in the manifest, a warning is issued, but processing continues;
- when multiple L1A/S products belonging to different data-takes are provided together with the corresponding L0N
  products, L1 and L0N products are paired automatically, matching mission, acquisition mode, absolute orbit number and
  datatake ID in the product names (if several L0N products match, the first one is used and a warning is issued);
- the output folder must be different from the input folders, to avoid polluting the input products.

## Example

```xml title="JobOrder.xml"
<?xml version="1.0" encoding="UTF-8"?>
<Ipf_Job_Order>
  <Ipf_Conf>
    <Processor_Name>S1_L1N_IPF</Processor_Name>
    <Version>1.1.0</Version>
    <Stdout_Log_Level>INFO</Stdout_Log_Level>
    <Stderr_Log_Level>INFO</Stderr_Log_Level>
    <Test>false</Test>
    <Breakpoint_Enable>false</Breakpoint_Enable>
    <Processing_Station>MTI</Processing_Station>
    <List_of_Dynamic_Processing_Parameters count="1">
      <Dynamic_Processing_Parameter>
        <Name>Mission_Id</Name>
        <Value>S1C</Value>
      </Dynamic_Processing_Parameter>
    </List_of_Dynamic_Processing_Parameters>
  </Ipf_Conf>
  <List_of_Ipf_Procs count="1">
    <Ipf_Proc>
      <Task_Name>S1_L1N_IPF</Task_Name>
      <Task_Version>1.1.0</Task_Version>
      <List_of_Inputs count="7">
        <Input>
          <File_Type>IW_SLC__1A</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./input-s1c/S1C_IW_SLC__1ADV_..._0067C8_B378.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>IW_RAW__0N</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./input-s1c/S1C_IW_RAW__0NDV_..._0067C8_9086.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>IW_SLC__1A</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./input-s1c/S1C_IW_SLC__1ADH_..._006C0A_53FA.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>IW_RAW__0N</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./input-s1c/S1C_IW_RAW__0NDH_..._006C0A_5DA9.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>AUX_PP1</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./S1C_AUX_PP1_V20250224T000000_G20250521T131402.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>AUX_CAL</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./S1C_AUX_CAL_V20250224T000000_G20250519T123501.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
        <Input>
          <File_Type>AUX_INS</File_Type>
          <File_Name_Type>Physical</File_Name_Type>
          <List_of_File_Names count="1">
            <File_Name>./S1C_AUX_INS_V20250224T000000_G20251015T082332.SAFE</File_Name>
          </List_of_File_Names>
        </Input>
      </List_of_Inputs>
      <List_of_Outputs count="1">
        <Output>
          <File_Type>DENOISE</File_Type>
          <File_Name_Type>Directory</File_Name_Type>
          <File_Name>./output</File_Name>
        </Output>
      </List_of_Outputs>
    </Ipf_Proc>
  </List_of_Ipf_Procs>
</Ipf_Job_Order>
```

## Supported file types

| Role                    | Supported `File_Type` values                                                                                                                                                         |
|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| L1 products to process  | `IW_SLC__1A`, `IW_SLC__1S`, `EW_SLC__1A`, `EW_SLC__1S`, `IW_GRDH_1A`, `IW_GRDH_1S`, `IW_GRDM_1A`, `IW_GRDM_1S`, `EW_GRDH_1A`, `EW_GRDH_1S`, `EW_GRDM_1A`, `EW_GRDM_1S`               |
| L0N products (optional) | `EW_RAW__0N`, `IW_RAW__0N`                                                                                                                                                           |
| Auxiliary files         | `AUX_PP1`, `AUX_CAL`, `AUX_INS`                                                                                                                                                      |
| Output                  | `DENOISE` (the only supported `File_Name_Type` is `Directory`)                                                                                                                       |

## Log level

The log level can be set through `<Stdout_Log_Level>` and `<Stderr_Log_Level>`: it can be either `INFO` (default) or
`DEBUG` (for development purposes only).
