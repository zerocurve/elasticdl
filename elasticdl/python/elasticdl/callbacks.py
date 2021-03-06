# Copyright 2020 The ElasticDL Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import shutil

import numpy as np
import tensorflow as tf
from tensorflow.keras import backend as K

from elasticdl.proto import elasticdl_pb2
from elasticdl.python.common.constants import Mode


class SavedModelExporter(tf.keras.callbacks.Callback):
    """Export model using SavedModel after training.
    Args:
        task_data_service: TaskDataService to process data according the task
        dataset_fn: function to process dataset
        model_handler: to transform the trained model with ElasticDL embedding
            layer to Keras native model.
    """

    def __init__(self, task_data_service, dataset_fn, model_handler):
        self._model_handler = model_handler
        self._task_data_service = task_data_service
        self._dataset_fn = dataset_fn

    def on_train_end(self, logs=None):
        """Call on the train job end
        Args:
            logs: dict. Currently no data is passed to this argument for this
                method but that may change in the future.
        """
        saved_model_path = self.params.get("saved_model_path", None)
        if saved_model_path is None or saved_model_path == "":
            return
        batch_size = self.params.get("batch_size")
        task = self._task_data_service.get_train_end_callback_task()
        dataset = self._task_data_service.get_dataset_by_task(task)
        if dataset is not None:
            dataset = self._dataset_fn(
                dataset,
                Mode.PREDICTION,
                self._task_data_service.data_reader.metadata,
            )
            dataset = dataset.batch(batch_size)
            model = self._model_handler.get_model_to_export(
                self.model, dataset
            )
            if os.path.exists(saved_model_path):
                shutil.rmtree(saved_model_path)

            # tf.saved_model cannot save the model if model.optimizer is not
            # None and the model is not complied.
            model.optimizer = None
            tf.saved_model.save(model, saved_model_path)


class MaxStepsStopping(tf.keras.callbacks.Callback):
    """Stop training if the training steps exceed the maximum.

    Args:
        max_steps:

    Example:
    ```python
    from elasticdl.python.elasticdl.callbacks import MaxStepsStopping

    def callbacks():
        # This callback will stop the training when the training steps
        # exceed the max_steps.
        max_steps_stopping = MaxStepsStopping(max_steps=1000)
        return [max_steps_stopping]
    ```
    """

    def __init__(self, max_steps):
        self._max_steps = max_steps
        self._completed_steps = 0

    def set_completed_steps(self, completed_steps):
        """We need to set completed steps if we load the model from
        a checkpoint where the model has been trained.
        """
        self._completed_steps = completed_steps

    def on_task_end(self, task, logs=None):
        """Call on the task end
        Args:
            task: A completed task.
            logs: dict. Currently no data is passed to this argument for this
                method but that may change in the future.
        """
        batch_size = self.params.get("batch_size", None)
        if task.type == elasticdl_pb2.TRAINING:
            task_records = task.end - task.start
            task_batch_count = int(task_records / batch_size)
            self._completed_steps += task_batch_count
            if self._completed_steps > self._max_steps:
                self.model.stop_training = True


class LearningRateScheduler(tf.keras.callbacks.Callback):
    """Learning rate scheduler schedule the learning rate according
    to the iteration steps.

    Args:
        schedule: A function that takes a batch index as input
        (integer, indexed from 0) and returns a new learning rate
        as output (float).

    Example:
    ```python
    from elasticdl.python.elasticdl.callbacks import LearningRateScheduler

    def callbacks():
        # This callback will schedule the learning rate for each step.
        def _schedule(batch):
            return 0.002 if batch < 1000 else 0.001
        learning_rate_scheduler = LearningRateScheduler(_schedule)
        return [learning_rate_scheduler]
    ```
    """

    def __init__(self, schedule):
        self._schedule = schedule

    def on_train_batch_begin(self, batch, logs=None):
        """
        Args:
            batch: integer, the model version requested from PS.
            logs: dict. Has keys batch and size representing the current batch
                number and the size of the batch.
        """
        if not hasattr(self.model.optimizer, "lr"):
            raise ValueError('Optimizer must have a "lr" attribute.')

        lr = self._schedule(batch)
        if not isinstance(lr, (tf.Tensor, float, np.float32, np.float64)):
            raise ValueError(
                'The output of the "schedule" function should be float.'
            )
        K.set_value(self.model.optimizer.lr, K.get_value(lr))
