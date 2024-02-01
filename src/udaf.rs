use std::sync::Arc;

use pyo3::{prelude::*, types::PyBool, types::PyTuple};

use datafusion::arrow::array::{Array, ArrayRef};
use datafusion::arrow::datatypes::DataType;
use datafusion::arrow::pyarrow::{PyArrowType, ToPyArrow};
use datafusion::common::ScalarValue;
use datafusion::error::{DataFusionError, Result};
use datafusion_expr::{create_udaf, Accumulator, AccumulatorFactoryFunction, AggregateUDF};

use crate::utils::parse_volatility;

#[derive(Debug)]
struct RustAccumulator {
    accum: PyObject,
}

impl RustAccumulator {
    fn new(accum: PyObject) -> Self {
        Self { accum }
    }
}

impl Accumulator for RustAccumulator {
    fn state(&self) -> Result<Vec<ScalarValue>> {
        Python::with_gil(|py| self.accum.as_ref(py).call_method0("state")?.extract())
            .map_err(|e| DataFusionError::Execution(format!("{e}")))
    }

    fn evaluate(&self) -> Result<ScalarValue> {
        Python::with_gil(|py| self.accum.as_ref(py).call_method0("evaluate")?.extract())
            .map_err(|e| DataFusionError::Execution(format!("{e}")))
    }

    fn update_batch(&mut self, values: &[ArrayRef]) -> Result<()> {
        Python::with_gil(|py| {
            // 1. cast args to Pyarrow array
            let py_args = values
                .iter()
                .map(|arg| arg.into_data().to_pyarrow(py).unwrap())
                .collect::<Vec<_>>();
            let py_args = PyTuple::new(py, py_args);

            // 2. call function
            self.accum
                .as_ref(py)
                .call_method1("update", py_args)
                .map_err(|e| DataFusionError::Execution(format!("{e}")))?;

            Ok(())
        })
    }

    fn merge_batch(&mut self, states: &[ArrayRef]) -> Result<()> {
        Python::with_gil(|py| {
            let state = &states[0];

            // 1. cast states to Pyarrow array
            let state = state
                .into_data()
                .to_pyarrow(py)
                .map_err(|e| DataFusionError::Execution(format!("{e}")))?;

            // 2. call merge
            self.accum
                .as_ref(py)
                .call_method1("merge", (state,))
                .map_err(|e| DataFusionError::Execution(format!("{e}")))?;

            Ok(())
        })
    }

    fn size(&self) -> usize {
        std::mem::size_of_val(self)
    }

    fn retract_batch(&mut self, values: &[ArrayRef]) -> Result<()> {
        Python::with_gil(|py| {
            // 1. cast args to Pyarrow array
            let py_args = values
                .iter()
                .map(|arg| arg.into_data().to_pyarrow(py).unwrap())
                .collect::<Vec<_>>();
            let py_args = PyTuple::new(py, py_args);

            // 2. call function
            self.accum
                .as_ref(py)
                .call_method1("retract_batch", py_args)
                .map_err(|e| DataFusionError::Execution(format!("{e}")))?;

            Ok(())
        })
    }

    fn supports_retract_batch(&self) -> bool {
        Python::with_gil(|py| {
            let x: Result<&PyAny, PyErr> =
                self.accum.as_ref(py).call_method0("supports_retract_batch");
            let x: &PyAny = x.unwrap_or(PyBool::new(py, false));
            x.extract().unwrap_or(false)
        })
    }
}

pub fn to_rust_accumulator(accum: PyObject) -> AccumulatorFactoryFunction {
    Arc::new(move |_| -> Result<Box<dyn Accumulator>> {
        let accum = Python::with_gil(|py| {
            accum
                .call0(py)
                .map_err(|e| DataFusionError::Execution(format!("{e}")))
        })?;
        Ok(Box::new(RustAccumulator::new(accum)))
    })
}

/// Represents an AggregateUDF
#[pyclass(name = "AggregateUDF", module = "datafusion", subclass)]
#[derive(Debug, Clone)]
pub struct PyAggregateUDF {
    pub(crate) function: AggregateUDF,
}

#[pymethods]
impl PyAggregateUDF {
    #[new]
    fn new(
        name: &str,
        accumulator: PyObject,
        input_type: PyArrowType<DataType>,
        return_type: PyArrowType<DataType>,
        state_type: PyArrowType<Vec<DataType>>,
        volatility: &str,
    ) -> PyResult<Self> {
        let function = create_udaf(
            name,
            vec![input_type.0],
            Arc::new(return_type.0),
            parse_volatility(volatility)?,
            to_rust_accumulator(accumulator),
            Arc::new(state_type.0),
        );
        Ok(Self { function })
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(format!("AggregateUDF({})", self.function.name()))
    }
}